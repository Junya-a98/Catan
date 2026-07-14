"""Immutable, JSON-safe house-rule configuration.

The standard rules are represented by every option being disabled.  Keeping
the model independent from Pygame, persistence, and networking lets those
boundaries share one strict document format without duplicating validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import hashlib
import json
from typing import Any

from game.development_cards import (
    DEVELOPMENT_CARD_LABELS,
    DevelopmentCardType,
)


_DOCUMENT_KEYS = frozenset(
    {
        "bank_trade_3_to_1",
        "skip_discard_on_seven",
        "disabled_development_cards",
    }
)


@dataclass(frozen=True)
class HouseRules:
    """Validated house rules with official behaviour as the default.

    ``disabled_development_cards`` is deliberately a ``frozenset`` so the
    value remains safely hashable and cannot change underneath a running
    match, replay recorder, or LAN room.
    """

    bank_trade_3_to_1: bool = False
    skip_discard_on_seven: bool = False
    disabled_development_cards: frozenset[DevelopmentCardType] = field(
        default_factory=frozenset
    )

    def __post_init__(self) -> None:
        if type(self.bank_trade_3_to_1) is not bool:
            raise ValueError("bank_trade_3_to_1 は真偽値で指定してください。")
        if type(self.skip_discard_on_seven) is not bool:
            raise ValueError("skip_discard_on_seven は真偽値で指定してください。")
        if not isinstance(self.disabled_development_cards, frozenset):
            raise ValueError(
                "disabled_development_cards は frozenset で指定してください。"
            )
        if any(
            type(card_type) is not DevelopmentCardType
            for card_type in self.disabled_development_cards
        ):
            raise ValueError("無効化する発展カード種別が不正です。")

    @classmethod
    def standard(cls) -> HouseRules:
        """Return the official/default configuration with no house rules."""

        return cls()

    @classmethod
    def from_document(cls, document: Mapping[str, Any] | None) -> HouseRules:
        """Parse the strict JSON document used by saves, replays, and LAN.

        ``None`` means the entire field was absent in a legacy document and
        therefore restores the standard rules.  A present document must be
        complete: silently defaulting a misspelled or partially written rule
        would make a recorded match non-reproducible.
        """

        if document is None:
            return cls.standard()
        if not isinstance(document, Mapping):
            raise ValueError("ハウスルール設定はオブジェクトで指定してください。")

        keys = set(document)
        if keys != _DOCUMENT_KEYS:
            unknown = sorted(str(key) for key in keys - _DOCUMENT_KEYS)
            missing = sorted(_DOCUMENT_KEYS - keys)
            detail = []
            if unknown:
                detail.append(f"未知: {', '.join(unknown)}")
            if missing:
                detail.append(f"不足: {', '.join(missing)}")
            suffix = f"（{' / '.join(detail)}）" if detail else ""
            raise ValueError(f"ハウスルール設定の項目が不正です。{suffix}")

        bank_trade = document["bank_trade_3_to_1"]
        skip_discard = document["skip_discard_on_seven"]
        disabled_names = document["disabled_development_cards"]
        if type(bank_trade) is not bool:
            raise ValueError("bank_trade_3_to_1 は真偽値で指定してください。")
        if type(skip_discard) is not bool:
            raise ValueError("skip_discard_on_seven は真偽値で指定してください。")
        if not isinstance(disabled_names, list):
            raise ValueError("disabled_development_cards は配列で指定してください。")
        if len(disabled_names) > len(DevelopmentCardType):
            raise ValueError("無効化する発展カード種別が多すぎます。")

        disabled_cards: set[DevelopmentCardType] = set()
        for name in disabled_names:
            if not isinstance(name, str):
                raise ValueError("発展カード種別名は文字列で指定してください。")
            try:
                card_type = DevelopmentCardType[name]
            except KeyError as exc:
                raise ValueError(f"未知の発展カード種別です: {name}") from exc
            if card_type in disabled_cards:
                raise ValueError(f"発展カード種別が重複しています: {name}")
            disabled_cards.add(card_type)

        return cls(
            bank_trade_3_to_1=bank_trade,
            skip_discard_on_seven=skip_discard,
            disabled_development_cards=frozenset(disabled_cards),
        )

    def to_document(self) -> dict[str, Any]:
        """Return a fresh, deterministic JSON-safe representation."""

        return {
            "bank_trade_3_to_1": self.bank_trade_3_to_1,
            "skip_discard_on_seven": self.skip_discard_on_seven,
            "disabled_development_cards": [
                card_type.name
                for card_type in DevelopmentCardType
                if card_type in self.disabled_development_cards
            ],
        }

    def toggle_development_card(
        self,
        card_type: DevelopmentCardType,
    ) -> HouseRules:
        """Return a new configuration with one card type toggled."""

        if type(card_type) is not DevelopmentCardType:
            raise ValueError("切り替える発展カード種別が不正です。")
        disabled = set(self.disabled_development_cards)
        if card_type in disabled:
            disabled.remove(card_type)
        else:
            disabled.add(card_type)
        return replace(
            self,
            disabled_development_cards=frozenset(disabled),
        )

    def compact_label(self) -> str:
        """Return a concise Japanese summary suitable for setup UIs."""

        labels = []
        if self.bank_trade_3_to_1:
            labels.append("銀行3:1")
        if self.skip_discard_on_seven:
            labels.append("7捨て札なし")
        disabled = [
            card_type
            for card_type in DevelopmentCardType
            if card_type in self.disabled_development_cards
        ]
        if len(disabled) == 1:
            labels.append(f"禁止:{DEVELOPMENT_CARD_LABELS[disabled[0]]}")
        elif disabled:
            labels.append(f"発展{len(disabled)}種禁止")
        return " / ".join(labels) if labels else "なし（標準）"

    def fingerprint(self) -> str:
        """Return a stable SHA-256 identity for replay/Web metadata."""

        encoded = json.dumps(
            self.to_document(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = ("HouseRules",)
