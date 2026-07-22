"""Pure authority domain for Trade 2.0 public auctions.

The domain is deliberately independent from ``Player``, ``CatanGame``, the
network protocol, and presentation code.  It owns only immutable, public
auction state and emits exact resource-ledger work for the integration layer
to commit atomically with a state replacement.

V1 uses open bids.  A seller escrows one resource bundle and publishes a
minimum *total card count*, rather than requiring particular resource types.
This keeps heterogeneous bids comparable by the seller (for example, two wood
versus one wheat plus one sheep).  Every bidder has at most one public bid per
auction and may revise or cancel it using the auction revision.  Acceptance is
all-or-nothing; partial awards and deferred consideration belong to later
protocol versions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any

from game.bank import RESOURCE_TYPES
from game.resource_ledger import MAX_RESOURCE_COUNT
from game.resources import ResourceType
from game.trade_market import LEDGER_CONSUME, LEDGER_RELEASE, LEDGER_RESERVE


TRADE_AUCTION_FORMAT = "catan-trade-auction"
TRADE_AUCTION_VERSION = 1

MIN_AUCTION_TTL = 1
MAX_AUCTION_TTL = 8
MAX_OPEN_AUCTIONS_PER_SELLER = 2
MAX_OPEN_AUCTIONS = 8
MAX_BIDS_PER_AUCTION = 3
MAX_AUCTION_BUNDLE_CARDS = MAX_RESOURCE_COUNT
MAX_PLAYER_INDEX = 3
MAX_TURN = 2_147_483_647
MAX_REVISION = 2_147_483_647
MAX_AUCTION_SEQUENCE = 999_999_999
EXHAUSTED_NEXT_SEQUENCE = MAX_AUCTION_SEQUENCE + 1

LEDGER_REPLACE = "replace"

AUCTION_CREATE = "create"
AUCTION_BID = "bid"
AUCTION_CANCEL_BID = "cancel_bid"
AUCTION_ACCEPT = "accept"
AUCTION_CANCEL = "cancel"
AUCTION_EXPIRE = "expire"

_AUCTION_OPERATIONS = frozenset(
    {
        AUCTION_CREATE,
        AUCTION_BID,
        AUCTION_CANCEL_BID,
        AUCTION_ACCEPT,
        AUCTION_CANCEL,
        AUCTION_EXPIRE,
    }
)
_LEDGER_OPERATIONS = frozenset(
    {LEDGER_RESERVE, LEDGER_REPLACE, LEDGER_RELEASE, LEDGER_CONSUME}
)
_DOCUMENT_KEYS = frozenset(
    {"format", "version", "next_sequence", "open_auctions"}
)
_AUCTION_DOCUMENT_KEYS = frozenset(
    {
        "auction_id",
        "seller_index",
        "offer",
        "minimum_bid_cards",
        "created_turn",
        "expires_turn",
        "revision",
        "bids",
    }
)
_BID_DOCUMENT_KEYS = frozenset({"bidder_index", "offer", "revision"})
_RESOURCE_SET = frozenset(RESOURCE_TYPES)
_RESOURCE_NAMES = frozenset(resource.name for resource in RESOURCE_TYPES)
_AUCTION_ID_PATTERN = re.compile(r"auction-[0-9]{9}\Z")
_RESERVATION_ID_PATTERN = re.compile(
    r"auction:auction-[0-9]{9}:(?:seller|bid-[0-3])\Z"
)


class TradeAuctionError(ValueError):
    """Raised when auction input, state, or a mutation plan is invalid."""


class AuctionConflictError(TradeAuctionError):
    """Raised when an auction revision or optimistic plan is stale."""


class AuctionPermissionError(TradeAuctionError):
    """Raised when the actor cannot perform an auction operation."""


class AuctionCapacityError(TradeAuctionError):
    """Raised when an auction or bid capacity has been reached."""


@dataclass(frozen=True)
class AuctionBid:
    """One bidder's immutable public escrow offer."""

    bidder_index: int
    offer: Mapping[ResourceType, int]
    revision: int = 1

    def __post_init__(self) -> None:
        _validate_player_index(self.bidder_index, label="bidder_index")
        object.__setattr__(
            self,
            "offer",
            MappingProxyType(_canonical_bundle(self.offer, label="bid.offer")),
        )
        _validate_int_range(
            self.revision,
            label="bid.revision",
            minimum=1,
            maximum=MAX_REVISION,
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "bidder_index": self.bidder_index,
            "offer": _bundle_to_document(self.offer),
            "revision": self.revision,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> AuctionBid:
        _expect_exact_keys(document, _BID_DOCUMENT_KEYS, "公開入札")
        return cls(
            bidder_index=document["bidder_index"],
            offer=_bundle_from_document(document["offer"], label="bid.offer"),
            revision=document["revision"],
        )


@dataclass(frozen=True)
class AuctionLot:
    """One open seller lot and its bidder-index-sorted public bids."""

    auction_id: str
    seller_index: int
    offer: Mapping[ResourceType, int]
    minimum_bid_cards: int
    created_turn: int
    expires_turn: int
    revision: int = 1
    bids: tuple[AuctionBid, ...] | list[AuctionBid] = ()

    def __post_init__(self) -> None:
        _validate_auction_id(self.auction_id)
        _validate_player_index(self.seller_index, label="seller_index")
        offer = _canonical_bundle(self.offer, label="auction.offer")
        minimum_bid_cards = _validate_int_range(
            self.minimum_bid_cards,
            label="minimum_bid_cards",
            minimum=1,
            maximum=MAX_AUCTION_BUNDLE_CARDS,
        )
        created_turn = _validate_turn(self.created_turn, label="created_turn")
        expires_turn = _validate_turn(self.expires_turn, label="expires_turn")
        ttl = expires_turn - created_turn
        if not MIN_AUCTION_TTL <= ttl <= MAX_AUCTION_TTL:
            raise TradeAuctionError(
                "競売期限は作成から "
                f"{MIN_AUCTION_TTL}〜{MAX_AUCTION_TTL} 手番後にしてください。"
            )
        revision = _validate_int_range(
            self.revision,
            label="auction.revision",
            minimum=1,
            maximum=MAX_REVISION,
        )
        if not isinstance(self.bids, (tuple, list)):
            raise TradeAuctionError("bidsは入札配列で指定してください。")
        if len(self.bids) > MAX_BIDS_PER_AUCTION:
            raise AuctionCapacityError(
                f"1競売の入札は {MAX_BIDS_PER_AUCTION} 件以下です。"
            )

        bids: list[AuctionBid] = []
        previous_bidder: int | None = None
        bid_revision_total = 0
        for bid in self.bids:
            if type(bid) is not AuctionBid:
                raise TradeAuctionError("bidsにはAuctionBidだけを指定してください。")
            if bid.bidder_index == self.seller_index:
                raise TradeAuctionError("売主は自分の競売へ入札できません。")
            if previous_bidder is not None and bid.bidder_index <= previous_bidder:
                raise TradeAuctionError(
                    "bidsは重複のないbidder_index昇順で指定してください。"
                )
            if set(offer).intersection(bid.offer):
                raise TradeAuctionError(
                    "売主の出品資源を同じ競売の入札へ含めることはできません。"
                )
            if _bundle_total(bid.offer) < minimum_bid_cards:
                raise TradeAuctionError("最低入札枚数に達していない入札があります。")
            bids.append(bid)
            bid_revision_total += bid.revision
            previous_bidder = bid.bidder_index

        # Every placement/update increments the lot revision.  Cancellations
        # can create gaps, so only the lower bound is knowable from open bids.
        if revision < 1 + bid_revision_total:
            raise TradeAuctionError("競売revisionと入札revisionが矛盾しています。")

        object.__setattr__(self, "offer", MappingProxyType(offer))
        object.__setattr__(self, "minimum_bid_cards", minimum_bid_cards)
        object.__setattr__(self, "created_turn", created_turn)
        object.__setattr__(self, "expires_turn", expires_turn)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "bids", tuple(bids))

    @property
    def seller_reservation_id(self) -> str:
        return f"auction:{self.auction_id}:seller"

    def bid_reservation_id(self, bidder_index: int) -> str:
        bidder_index = _validate_player_index(bidder_index, label="bidder_index")
        return f"auction:{self.auction_id}:bid-{bidder_index}"

    def get_bid(self, bidder_index: int) -> AuctionBid | None:
        bidder_index = _validate_player_index(bidder_index, label="bidder_index")
        return next(
            (bid for bid in self.bids if bid.bidder_index == bidder_index),
            None,
        )

    def is_expired(self, current_turn: int) -> bool:
        return _validate_turn(current_turn, label="current_turn") >= self.expires_turn

    def to_document(self) -> dict[str, Any]:
        return {
            "auction_id": self.auction_id,
            "seller_index": self.seller_index,
            "offer": _bundle_to_document(self.offer),
            "minimum_bid_cards": self.minimum_bid_cards,
            "created_turn": self.created_turn,
            "expires_turn": self.expires_turn,
            "revision": self.revision,
            "bids": [bid.to_document() for bid in self.bids],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> AuctionLot:
        _expect_exact_keys(document, _AUCTION_DOCUMENT_KEYS, "公開競売")
        raw_bids = document["bids"]
        if not isinstance(raw_bids, list):
            raise TradeAuctionError("bidsは配列で指定してください。")
        return cls(
            auction_id=document["auction_id"],
            seller_index=document["seller_index"],
            offer=_bundle_from_document(document["offer"], label="auction.offer"),
            minimum_bid_cards=document["minimum_bid_cards"],
            created_turn=document["created_turn"],
            expires_turn=document["expires_turn"],
            revision=document["revision"],
            bids=tuple(AuctionBid.from_document(item) for item in raw_bids),
        )


@dataclass(frozen=True)
class AuctionLedgerMutation:
    """One exact reservation mutation for an existing ``ResourceLedger``."""

    operation: str
    player_index: int
    reservation_id: str
    bundle: Mapping[ResourceType, int]
    previous_bundle: Mapping[ResourceType, int] | None = None

    def __post_init__(self) -> None:
        if type(self.operation) is not str or self.operation not in _LEDGER_OPERATIONS:
            raise TradeAuctionError("未対応の競売資源台帳操作です。")
        _validate_player_index(self.player_index, label="player_index")
        _validate_reservation_id(self.reservation_id)
        bundle = _canonical_bundle(self.bundle, label="ledger.bundle")
        previous = self.previous_bundle
        if self.operation == LEDGER_REPLACE:
            if previous is None:
                raise TradeAuctionError("予約差替にはprevious_bundleが必要です。")
            previous = MappingProxyType(
                _canonical_bundle(previous, label="ledger.previous_bundle")
            )
            if dict(previous) == bundle:
                raise TradeAuctionError("同一bundleへの予約差替はできません。")
        elif previous is not None:
            raise TradeAuctionError(
                "予約差替以外にprevious_bundleは指定できません。"
            )
        object.__setattr__(self, "bundle", MappingProxyType(bundle))
        object.__setattr__(self, "previous_bundle", previous)


@dataclass(frozen=True)
class AuctionResourceTransfer:
    """Exact accepted-auction movement to commit after escrow validation."""

    from_player_index: int
    to_player_index: int
    bundle: Mapping[ResourceType, int]
    source_reservation_id: str

    def __post_init__(self) -> None:
        _validate_player_index(self.from_player_index, label="from_player_index")
        _validate_player_index(self.to_player_index, label="to_player_index")
        if self.from_player_index == self.to_player_index:
            raise TradeAuctionError("資源移動元と移動先は別プレイヤーにしてください。")
        _validate_reservation_id(self.source_reservation_id)
        object.__setattr__(
            self,
            "bundle",
            MappingProxyType(_canonical_bundle(self.bundle, label="transfer.bundle")),
        )


@dataclass(frozen=True)
class AuctionMutationPlan:
    """Process-local optimistic plan produced only by ``AuctionHouse``."""

    operation: str
    base_fingerprint: str
    actor_index: int | None
    current_turn: int | None
    created_auction: AuctionLot | None
    updated_auction: AuctionLot | None
    removed_auctions: tuple[AuctionLot, ...]
    accepted_bid: AuctionBid | None
    ledger_mutations: tuple[AuctionLedgerMutation, ...]
    transfers: tuple[AuctionResourceTransfer, ...]


@dataclass(frozen=True)
class AuctionMutationResult:
    """Candidate authority state and its all-or-nothing resource work."""

    house: AuctionHouse
    operation: str
    created_auction: AuctionLot | None
    updated_auction: AuctionLot | None
    removed_auctions: tuple[AuctionLot, ...]
    accepted_bid: AuctionBid | None
    ledger_mutations: tuple[AuctionLedgerMutation, ...]
    transfers: tuple[AuctionResourceTransfer, ...]


class AuctionHouse:
    """Immutable authority snapshot for all currently open public auctions."""

    __slots__ = ("_next_sequence", "_auctions")

    def __init__(
        self,
        *,
        next_sequence: int = 0,
        open_auctions: tuple[AuctionLot, ...] | list[AuctionLot] = (),
    ) -> None:
        self._next_sequence = _validate_next_sequence(next_sequence)
        if not isinstance(open_auctions, (tuple, list)):
            raise TradeAuctionError("open_auctionsは競売配列で指定してください。")
        if len(open_auctions) > MAX_OPEN_AUCTIONS:
            raise AuctionCapacityError(
                f"公開競売は全体で {MAX_OPEN_AUCTIONS} 件以下です。"
            )

        auctions: dict[str, AuctionLot] = {}
        seller_counts: dict[int, int] = {}
        previous_id: str | None = None
        for auction in open_auctions:
            if type(auction) is not AuctionLot:
                raise TradeAuctionError(
                    "open_auctionsにはAuctionLotだけを指定してください。"
                )
            if previous_id is not None and auction.auction_id <= previous_id:
                raise TradeAuctionError(
                    "open_auctionsは重複のないauction_id昇順で指定してください。"
                )
            sequence = _sequence_from_auction_id(auction.auction_id)
            if sequence >= self._next_sequence:
                raise TradeAuctionError(
                    "公開競売のsequenceはnext_sequence未満にしてください。"
                )
            seller_counts[auction.seller_index] = (
                seller_counts.get(auction.seller_index, 0) + 1
            )
            if seller_counts[auction.seller_index] > MAX_OPEN_AUCTIONS_PER_SELLER:
                raise AuctionCapacityError(
                    "1人の公開競売は "
                    f"{MAX_OPEN_AUCTIONS_PER_SELLER} 件以下です。"
                )
            auctions[auction.auction_id] = auction
            previous_id = auction.auction_id
        self._auctions = auctions

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    @property
    def open_auctions(self) -> tuple[AuctionLot, ...]:
        return tuple(
            self._auctions[auction_id] for auction_id in sorted(self._auctions)
        )

    def get_auction(self, auction_id: str) -> AuctionLot | None:
        return self._auctions.get(_validate_auction_id(auction_id))

    def plan_create(
        self,
        *,
        seller_index: int,
        offer: Mapping[ResourceType, int],
        current_turn: int,
        ttl: int,
        minimum_bid_cards: int = 1,
    ) -> AuctionMutationPlan:
        """Prepare a public lot and escrow its complete seller bundle."""

        seller_index = _validate_player_index(seller_index, label="seller_index")
        current_turn = _validate_turn(current_turn, label="current_turn")
        ttl = _validate_int_range(
            ttl,
            label="ttl",
            minimum=MIN_AUCTION_TTL,
            maximum=MAX_AUCTION_TTL,
        )
        minimum_bid_cards = _validate_int_range(
            minimum_bid_cards,
            label="minimum_bid_cards",
            minimum=1,
            maximum=MAX_AUCTION_BUNDLE_CARDS,
        )
        if current_turn > MAX_TURN - ttl:
            raise TradeAuctionError("競売期限が手番上限を超えます。")
        if len(self._auctions) >= MAX_OPEN_AUCTIONS:
            raise AuctionCapacityError(
                f"公開競売は全体で {MAX_OPEN_AUCTIONS} 件までです。"
            )
        if sum(
            auction.seller_index == seller_index
            for auction in self._auctions.values()
        ) >= MAX_OPEN_AUCTIONS_PER_SELLER:
            raise AuctionCapacityError(
                f"1人の公開競売は {MAX_OPEN_AUCTIONS_PER_SELLER} 件までです。"
            )
        if self._next_sequence >= EXHAUSTED_NEXT_SEQUENCE:
            raise AuctionCapacityError("競売IDをこれ以上発行できません。")

        auction = AuctionLot(
            auction_id=_auction_id_for_sequence(self._next_sequence),
            seller_index=seller_index,
            offer=offer,
            minimum_bid_cards=minimum_bid_cards,
            created_turn=current_turn,
            expires_turn=current_turn + ttl,
            revision=1,
            bids=(),
        )
        mutation = AuctionLedgerMutation(
            operation=LEDGER_RESERVE,
            player_index=seller_index,
            reservation_id=auction.seller_reservation_id,
            bundle=auction.offer,
        )
        return AuctionMutationPlan(
            operation=AUCTION_CREATE,
            base_fingerprint=self.fingerprint(),
            actor_index=seller_index,
            current_turn=current_turn,
            created_auction=auction,
            updated_auction=None,
            removed_auctions=(),
            accepted_bid=None,
            ledger_mutations=(mutation,),
            transfers=(),
        )

    def plan_bid(
        self,
        *,
        bidder_index: int,
        auction_id: str,
        expected_revision: int,
        offer: Mapping[ResourceType, int],
        current_turn: int,
    ) -> AuctionMutationPlan:
        """Prepare a new bid or atomically replace this bidder's escrow."""

        bidder_index = _validate_player_index(bidder_index, label="bidder_index")
        current_turn = _validate_turn(current_turn, label="current_turn")
        auction = self._require_auction(auction_id, expected_revision)
        self._require_not_expired(auction, current_turn)
        if bidder_index == auction.seller_index:
            raise AuctionPermissionError("売主は自分の競売へ入札できません。")

        canonical_offer = _canonical_bundle(offer, label="bid.offer")
        if set(canonical_offer).intersection(auction.offer):
            raise TradeAuctionError(
                "売主の出品資源を同じ競売の入札へ含めることはできません。"
            )
        if _bundle_total(canonical_offer) < auction.minimum_bid_cards:
            raise TradeAuctionError(
                f"入札は合計 {auction.minimum_bid_cards} 枚以上にしてください。"
            )
        previous = auction.get_bid(bidder_index)
        if previous is None and len(auction.bids) >= MAX_BIDS_PER_AUCTION:
            raise AuctionCapacityError(
                f"1競売の入札は {MAX_BIDS_PER_AUCTION} 件までです。"
            )
        if previous is not None and dict(previous.offer) == canonical_offer:
            raise AuctionConflictError("同じ内容の入札へ更新することはできません。")

        next_bid_revision = (
            1 if previous is None else _increment_revision(previous.revision, "入札")
        )
        next_auction_revision = _increment_revision(auction.revision, "競売")
        bid = AuctionBid(
            bidder_index=bidder_index,
            offer=canonical_offer,
            revision=next_bid_revision,
        )
        updated = _with_bid(
            auction,
            bid=bid,
            revision=next_auction_revision,
        )
        reservation_id = auction.bid_reservation_id(bidder_index)
        if previous is None:
            ledger_mutation = AuctionLedgerMutation(
                operation=LEDGER_RESERVE,
                player_index=bidder_index,
                reservation_id=reservation_id,
                bundle=bid.offer,
            )
        else:
            ledger_mutation = AuctionLedgerMutation(
                operation=LEDGER_REPLACE,
                player_index=bidder_index,
                reservation_id=reservation_id,
                bundle=bid.offer,
                previous_bundle=previous.offer,
            )
        return AuctionMutationPlan(
            operation=AUCTION_BID,
            base_fingerprint=self.fingerprint(),
            actor_index=bidder_index,
            current_turn=current_turn,
            created_auction=None,
            updated_auction=updated,
            removed_auctions=(),
            accepted_bid=None,
            ledger_mutations=(ledger_mutation,),
            transfers=(),
        )

    def plan_cancel_bid(
        self,
        *,
        bidder_index: int,
        auction_id: str,
        expected_revision: int,
        current_turn: int,
    ) -> AuctionMutationPlan:
        """Prepare cancellation of the actor's own public bid."""

        bidder_index = _validate_player_index(bidder_index, label="bidder_index")
        current_turn = _validate_turn(current_turn, label="current_turn")
        auction = self._require_auction(auction_id, expected_revision)
        self._require_not_expired(auction, current_turn)
        bid = auction.get_bid(bidder_index)
        if bid is None:
            raise AuctionConflictError("取り消す公開入札がありません。")
        updated = _without_bid(
            auction,
            bidder_index=bidder_index,
            revision=_increment_revision(auction.revision, "競売"),
        )
        release = AuctionLedgerMutation(
            operation=LEDGER_RELEASE,
            player_index=bidder_index,
            reservation_id=auction.bid_reservation_id(bidder_index),
            bundle=bid.offer,
        )
        return AuctionMutationPlan(
            operation=AUCTION_CANCEL_BID,
            base_fingerprint=self.fingerprint(),
            actor_index=bidder_index,
            current_turn=current_turn,
            created_auction=None,
            updated_auction=updated,
            removed_auctions=(),
            accepted_bid=None,
            ledger_mutations=(release,),
            transfers=(),
        )

    def plan_accept(
        self,
        *,
        seller_index: int,
        auction_id: str,
        expected_revision: int,
        bidder_index: int,
        current_turn: int,
    ) -> AuctionMutationPlan:
        """Prepare seller-selected award, consuming both winning escrows."""

        seller_index = _validate_player_index(seller_index, label="seller_index")
        bidder_index = _validate_player_index(bidder_index, label="bidder_index")
        current_turn = _validate_turn(current_turn, label="current_turn")
        auction = self._require_auction(auction_id, expected_revision)
        self._require_not_expired(auction, current_turn)
        if auction.seller_index != seller_index:
            raise AuctionPermissionError("落札者を決定できるのは売主だけです。")
        winning_bid = auction.get_bid(bidder_index)
        if winning_bid is None:
            raise AuctionConflictError("選択した公開入札は存在しません。")

        ledger_mutations = [
            AuctionLedgerMutation(
                operation=LEDGER_CONSUME,
                player_index=seller_index,
                reservation_id=auction.seller_reservation_id,
                bundle=auction.offer,
            ),
            AuctionLedgerMutation(
                operation=LEDGER_CONSUME,
                player_index=bidder_index,
                reservation_id=auction.bid_reservation_id(bidder_index),
                bundle=winning_bid.offer,
            ),
        ]
        ledger_mutations.extend(
            AuctionLedgerMutation(
                operation=LEDGER_RELEASE,
                player_index=bid.bidder_index,
                reservation_id=auction.bid_reservation_id(bid.bidder_index),
                bundle=bid.offer,
            )
            for bid in auction.bids
            if bid.bidder_index != bidder_index
        )
        transfers = (
            AuctionResourceTransfer(
                from_player_index=seller_index,
                to_player_index=bidder_index,
                bundle=auction.offer,
                source_reservation_id=auction.seller_reservation_id,
            ),
            AuctionResourceTransfer(
                from_player_index=bidder_index,
                to_player_index=seller_index,
                bundle=winning_bid.offer,
                source_reservation_id=auction.bid_reservation_id(bidder_index),
            ),
        )
        return AuctionMutationPlan(
            operation=AUCTION_ACCEPT,
            base_fingerprint=self.fingerprint(),
            actor_index=seller_index,
            current_turn=current_turn,
            created_auction=None,
            updated_auction=None,
            removed_auctions=(auction,),
            accepted_bid=winning_bid,
            ledger_mutations=tuple(ledger_mutations),
            transfers=transfers,
        )

    def plan_cancel(
        self,
        *,
        seller_index: int,
        auction_id: str,
        expected_revision: int,
        current_turn: int,
    ) -> AuctionMutationPlan:
        """Prepare seller cancellation and release every participant escrow."""

        seller_index = _validate_player_index(seller_index, label="seller_index")
        current_turn = _validate_turn(current_turn, label="current_turn")
        auction = self._require_auction(auction_id, expected_revision)
        self._require_not_expired(auction, current_turn)
        if auction.seller_index != seller_index:
            raise AuctionPermissionError("競売を取り消せるのは売主だけです。")
        releases = self._release_mutations((auction,))
        return AuctionMutationPlan(
            operation=AUCTION_CANCEL,
            base_fingerprint=self.fingerprint(),
            actor_index=seller_index,
            current_turn=current_turn,
            created_auction=None,
            updated_auction=None,
            removed_auctions=(auction,),
            accepted_bid=None,
            ledger_mutations=releases,
            transfers=(),
        )

    def plan_expire(self, *, current_turn: int) -> AuctionMutationPlan:
        """Prepare deterministic batch expiry and release all related escrows."""

        current_turn = _validate_turn(current_turn, label="current_turn")
        expired = tuple(
            auction
            for auction in self.open_auctions
            if auction.is_expired(current_turn)
        )
        return AuctionMutationPlan(
            operation=AUCTION_EXPIRE,
            base_fingerprint=self.fingerprint(),
            actor_index=None,
            current_turn=current_turn,
            created_auction=None,
            updated_auction=None,
            removed_auctions=expired,
            accepted_bid=None,
            ledger_mutations=self._release_mutations(expired),
            transfers=(),
        )

    def apply(self, plan: AuctionMutationPlan) -> AuctionMutationResult:
        """Validate and functionally apply one authority-produced plan."""

        if type(plan) is not AuctionMutationPlan:
            raise TradeAuctionError("AuctionMutationPlanを指定してください。")
        if type(plan.operation) is not str or plan.operation not in _AUCTION_OPERATIONS:
            raise TradeAuctionError("未対応の競売操作です。")
        if plan.base_fingerprint != self.fingerprint():
            raise AuctionConflictError("競売状態が更新されたため再試行してください。")

        expected = self._rebuild_plan(plan)
        if plan != expected:
            raise AuctionConflictError("競売操作planが現在状態と一致しません。")

        auctions = dict(self._auctions)
        next_sequence = self._next_sequence
        if plan.operation == AUCTION_CREATE:
            assert plan.created_auction is not None
            auctions[plan.created_auction.auction_id] = plan.created_auction
            next_sequence += 1
        elif plan.operation in {AUCTION_BID, AUCTION_CANCEL_BID}:
            assert plan.updated_auction is not None
            auctions[plan.updated_auction.auction_id] = plan.updated_auction
        else:
            for auction in plan.removed_auctions:
                del auctions[auction.auction_id]

        ordered = tuple(auctions[key] for key in sorted(auctions))
        if next_sequence == self._next_sequence and ordered == self.open_auctions:
            next_house = self
        else:
            next_house = AuctionHouse(
                next_sequence=next_sequence,
                open_auctions=ordered,
            )
        return AuctionMutationResult(
            house=next_house,
            operation=plan.operation,
            created_auction=plan.created_auction,
            updated_auction=plan.updated_auction,
            removed_auctions=plan.removed_auctions,
            accepted_bid=plan.accepted_bid,
            ledger_mutations=plan.ledger_mutations,
            transfers=plan.transfers,
        )

    def _rebuild_plan(self, plan: AuctionMutationPlan) -> AuctionMutationPlan:
        if plan.operation == AUCTION_CREATE:
            auction = plan.created_auction
            if auction is None:
                raise AuctionConflictError("作成対象の競売がありません。")
            return self.plan_create(
                seller_index=auction.seller_index,
                offer=auction.offer,
                current_turn=auction.created_turn,
                ttl=auction.expires_turn - auction.created_turn,
                minimum_bid_cards=auction.minimum_bid_cards,
            )

        if plan.operation in {AUCTION_BID, AUCTION_CANCEL_BID}:
            updated = plan.updated_auction
            if updated is None or plan.actor_index is None or plan.current_turn is None:
                raise AuctionConflictError("更新対象の競売がありません。")
            current = self._auctions.get(updated.auction_id)
            if current is None:
                raise AuctionConflictError("指定した公開競売は存在しません。")
            if plan.operation == AUCTION_BID:
                bid = updated.get_bid(plan.actor_index)
                if bid is None:
                    raise AuctionConflictError("更新対象の入札がありません。")
                return self.plan_bid(
                    bidder_index=plan.actor_index,
                    auction_id=current.auction_id,
                    expected_revision=current.revision,
                    offer=bid.offer,
                    current_turn=plan.current_turn,
                )
            return self.plan_cancel_bid(
                bidder_index=plan.actor_index,
                auction_id=current.auction_id,
                expected_revision=current.revision,
                current_turn=plan.current_turn,
            )

        first = plan.removed_auctions[0] if plan.removed_auctions else None
        if plan.operation == AUCTION_ACCEPT:
            if (
                first is None
                or plan.actor_index is None
                or plan.current_turn is None
                or plan.accepted_bid is None
            ):
                raise AuctionConflictError("落札対象の競売または入札がありません。")
            return self.plan_accept(
                seller_index=plan.actor_index,
                auction_id=first.auction_id,
                expected_revision=first.revision,
                bidder_index=plan.accepted_bid.bidder_index,
                current_turn=plan.current_turn,
            )
        if plan.operation == AUCTION_CANCEL:
            if first is None or plan.actor_index is None or plan.current_turn is None:
                raise AuctionConflictError("取消対象の競売がありません。")
            return self.plan_cancel(
                seller_index=plan.actor_index,
                auction_id=first.auction_id,
                expected_revision=first.revision,
                current_turn=plan.current_turn,
            )
        if plan.operation == AUCTION_EXPIRE:
            if plan.current_turn is None:
                raise AuctionConflictError("期限判定手番がありません。")
            return self.plan_expire(current_turn=plan.current_turn)
        raise TradeAuctionError("未対応の競売操作です。")

    def _require_auction(
        self,
        auction_id: str,
        expected_revision: int,
    ) -> AuctionLot:
        auction_id = _validate_auction_id(auction_id)
        expected_revision = _validate_int_range(
            expected_revision,
            label="expected_revision",
            minimum=1,
            maximum=MAX_REVISION,
        )
        auction = self._auctions.get(auction_id)
        if auction is None:
            raise AuctionConflictError("指定した公開競売は存在しません。")
        if auction.revision != expected_revision:
            raise AuctionConflictError("公開競売のrevisionが更新されています。")
        return auction

    @staticmethod
    def _require_not_expired(auction: AuctionLot, current_turn: int) -> None:
        if auction.is_expired(current_turn):
            raise AuctionConflictError("期限切れの公開競売は操作できません。")

    @staticmethod
    def _release_mutations(
        auctions: tuple[AuctionLot, ...],
    ) -> tuple[AuctionLedgerMutation, ...]:
        releases: list[AuctionLedgerMutation] = []
        for auction in auctions:
            releases.append(
                AuctionLedgerMutation(
                    operation=LEDGER_RELEASE,
                    player_index=auction.seller_index,
                    reservation_id=auction.seller_reservation_id,
                    bundle=auction.offer,
                )
            )
            releases.extend(
                AuctionLedgerMutation(
                    operation=LEDGER_RELEASE,
                    player_index=bid.bidder_index,
                    reservation_id=auction.bid_reservation_id(bid.bidder_index),
                    bundle=bid.offer,
                )
                for bid in auction.bids
            )
        return tuple(releases)

    def to_document(self) -> dict[str, Any]:
        return {
            "format": TRADE_AUCTION_FORMAT,
            "version": TRADE_AUCTION_VERSION,
            "next_sequence": self._next_sequence,
            "open_auctions": [
                auction.to_document() for auction in self.open_auctions
            ],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> AuctionHouse:
        """Restore an exact canonical v1 authority state."""

        _expect_exact_keys(document, _DOCUMENT_KEYS, "公開競売場")
        if document["format"] != TRADE_AUCTION_FORMAT:
            raise TradeAuctionError("公開競売場formatが不正です。")
        if (
            type(document["version"]) is not int
            or document["version"] != TRADE_AUCTION_VERSION
        ):
            raise TradeAuctionError(
                f"公開競売場versionは {TRADE_AUCTION_VERSION} で指定してください。"
            )
        raw_auctions = document["open_auctions"]
        if not isinstance(raw_auctions, list):
            raise TradeAuctionError("open_auctionsは配列で指定してください。")
        return cls(
            next_sequence=document["next_sequence"],
            open_auctions=tuple(
                AuctionLot.from_document(item) for item in raw_auctions
            ),
        )

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_document(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _with_bid(auction: AuctionLot, *, bid: AuctionBid, revision: int) -> AuctionLot:
    bids = {existing.bidder_index: existing for existing in auction.bids}
    bids[bid.bidder_index] = bid
    return AuctionLot(
        auction_id=auction.auction_id,
        seller_index=auction.seller_index,
        offer=auction.offer,
        minimum_bid_cards=auction.minimum_bid_cards,
        created_turn=auction.created_turn,
        expires_turn=auction.expires_turn,
        revision=revision,
        bids=tuple(bids[index] for index in sorted(bids)),
    )


def _without_bid(
    auction: AuctionLot,
    *,
    bidder_index: int,
    revision: int,
) -> AuctionLot:
    return AuctionLot(
        auction_id=auction.auction_id,
        seller_index=auction.seller_index,
        offer=auction.offer,
        minimum_bid_cards=auction.minimum_bid_cards,
        created_turn=auction.created_turn,
        expires_turn=auction.expires_turn,
        revision=revision,
        bids=tuple(
            bid for bid in auction.bids if bid.bidder_index != bidder_index
        ),
    )


def _canonical_bundle(
    bundle: Mapping[ResourceType, int],
    *,
    label: str,
) -> dict[ResourceType, int]:
    if not isinstance(bundle, Mapping) or not bundle:
        raise TradeAuctionError(f"{label}は空でない資源mapで指定してください。")
    if len(bundle) > len(RESOURCE_TYPES) or any(
        type(resource) is not ResourceType or resource not in _RESOURCE_SET
        for resource in bundle
    ):
        raise TradeAuctionError(f"{label}には生産可能な5資源だけを指定してください。")
    canonical = {
        resource: _validate_int_range(
            bundle[resource],
            label=f"{label}.{resource.name}",
            minimum=1,
            maximum=MAX_RESOURCE_COUNT,
        )
        for resource in RESOURCE_TYPES
        if resource in bundle
    }
    if _bundle_total(canonical) > MAX_AUCTION_BUNDLE_CARDS:
        raise TradeAuctionError(
            f"{label}は合計 {MAX_AUCTION_BUNDLE_CARDS} 枚以下にしてください。"
        )
    return canonical


def _bundle_total(bundle: Mapping[ResourceType, int]) -> int:
    return sum(bundle.values())


def _bundle_to_document(bundle: Mapping[ResourceType, int]) -> dict[str, int]:
    canonical = _canonical_bundle(bundle, label="bundle")
    return {
        resource.name: canonical[resource]
        for resource in RESOURCE_TYPES
        if resource in canonical
    }


def _bundle_from_document(value: Any, *, label: str) -> dict[ResourceType, int]:
    if not isinstance(value, Mapping) or not value:
        raise TradeAuctionError(f"{label}は空でないobjectにしてください。")
    if len(value) > len(RESOURCE_TYPES) or any(
        type(name) is not str or name not in _RESOURCE_NAMES for name in value
    ):
        raise TradeAuctionError(f"{label}には生産可能な5資源だけを指定してください。")
    return _canonical_bundle(
        {
            resource: value[resource.name]
            for resource in RESOURCE_TYPES
            if resource.name in value
        },
        label=label,
    )


def _validate_player_index(value: Any, *, label: str) -> int:
    return _validate_int_range(
        value,
        label=label,
        minimum=0,
        maximum=MAX_PLAYER_INDEX,
    )


def _validate_turn(value: Any, *, label: str) -> int:
    return _validate_int_range(value, label=label, minimum=0, maximum=MAX_TURN)


def _validate_next_sequence(value: Any) -> int:
    return _validate_int_range(
        value,
        label="next_sequence",
        minimum=0,
        maximum=EXHAUSTED_NEXT_SEQUENCE,
    )


def _validate_int_range(
    value: Any,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise TradeAuctionError(
            f"{label}は {minimum} 以上 {maximum} 以下の整数で指定してください。"
        )
    return value


def _increment_revision(revision: int, label: str) -> int:
    revision = _validate_int_range(
        revision,
        label=f"{label}revision",
        minimum=1,
        maximum=MAX_REVISION,
    )
    if revision == MAX_REVISION:
        raise AuctionCapacityError(f"{label}revisionが上限に達しました。")
    return revision + 1


def _auction_id_for_sequence(sequence: int) -> str:
    _validate_int_range(
        sequence,
        label="auction sequence",
        minimum=0,
        maximum=MAX_AUCTION_SEQUENCE,
    )
    return f"auction-{sequence:09d}"


def _validate_auction_id(value: Any) -> str:
    if type(value) is not str or _AUCTION_ID_PATTERN.fullmatch(value) is None:
        raise TradeAuctionError("auction_idは安全な競売場発行IDで指定してください。")
    return value


def _validate_reservation_id(value: Any) -> str:
    if type(value) is not str or _RESERVATION_ID_PATTERN.fullmatch(value) is None:
        raise TradeAuctionError("競売予約IDが不正です。")
    return value


def _sequence_from_auction_id(auction_id: str) -> int:
    return int(_validate_auction_id(auction_id).removeprefix("auction-"))


def _expect_exact_keys(value: Any, expected: frozenset[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise TradeAuctionError(f"{label}の項目が不正です。")


__all__ = (
    "AUCTION_ACCEPT",
    "AUCTION_BID",
    "AUCTION_CANCEL",
    "AUCTION_CANCEL_BID",
    "AUCTION_CREATE",
    "AUCTION_EXPIRE",
    "EXHAUSTED_NEXT_SEQUENCE",
    "LEDGER_CONSUME",
    "LEDGER_RELEASE",
    "LEDGER_REPLACE",
    "LEDGER_RESERVE",
    "MAX_AUCTION_BUNDLE_CARDS",
    "MAX_AUCTION_TTL",
    "MAX_BIDS_PER_AUCTION",
    "MAX_OPEN_AUCTIONS",
    "MAX_OPEN_AUCTIONS_PER_SELLER",
    "MIN_AUCTION_TTL",
    "AuctionBid",
    "AuctionCapacityError",
    "AuctionConflictError",
    "AuctionHouse",
    "AuctionLedgerMutation",
    "AuctionLot",
    "AuctionMutationPlan",
    "AuctionMutationResult",
    "AuctionPermissionError",
    "AuctionResourceTransfer",
    "TRADE_AUCTION_FORMAT",
    "TRADE_AUCTION_VERSION",
    "TradeAuctionError",
)
