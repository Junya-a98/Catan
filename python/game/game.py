from collections.abc import Mapping
from dataclasses import asdict, replace
import math
import random
import secrets

import pygame

from game.ai import AI_ACTION_DELAY_MS, AI_SPEED_OPTIONS, SimpleAI
from game.ai_personality import (
    AI_PERSONALITY_MODES,
    AI_PERSONALITY_PROFILES,
    DISRUPTOR,
    EXPANSION,
    MIXED,
    STANDARD,
    TRADER,
    normalize_ai_personality,
    normalize_ai_personality_mode,
)
from game.audio import GameAudio
from game.bank import BANK_RESOURCE_COUNT, RESOURCE_TYPES, ResourceBank
from game.board_rules import BoardHighlightState, BoardRules
from game.building import Building, BuildingType
from game.constants import (
    COLORS,
    EDGE_SELECTION_RADIUS,
    HELP_PANEL_COLLAPSED_HEIGHT,
    HELP_PANEL_HEIGHT,
    HELP_PANEL_Y,
    LOG_PANEL_HEIGHT,
    LOG_PANEL_WIDTH,
    MAX_VICTORY_POINT_TARGET,
    MIN_LARGEST_ARMY_SIZE,
    MIN_LONGEST_ROAD_LENGTH,
    MIN_VICTORY_POINT_TARGET,
    NODE_SELECTION_RADIUS,
    SIDE_PANEL_WIDTH,
    SIDE_PANEL_X,
    ROBBER_DISCARD_THRESHOLD,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
    TILE_SELECTION_RADIUS,
    WINNING_VICTORY_POINTS,
    WINDOW_TITLE,
)
from game.custom_map import CustomMapError, CustomMapSpec
from game.dice_animation import DiceAnimationOverlay
from game.development_cards import (
    DEVELOPMENT_CARD_LABELS,
    DevelopmentCardType,
    create_development_deck,
)
from game.dice import roll_two_dice
from game.feedback import FeedbackManager
from game.forecast_events import (
    BANDIT_RAID_EVENT_ID,
    CAMPAIGN_FORECAST_CATALOG_ID,
    CONSTRUCTION_BOOM_EVENT_ID,
    EARTHQUAKE_EVENT_ID,
    FORECAST_EVENTS_KIND,
    HARBOR_BLOCKADE_EVENT_ID,
    MERCHANT_FESTIVAL_EVENT_ID,
    SHEEP_DROUGHT_EVENT_ID,
    WHEAT_HARVEST_EVENT_ID,
    event_definition,
)
from game.frontier import FRONTIER_KIND
from game.grand_campaign import GrandCampaignError, HarborBlockadePlan
from game.game_board import GameBoard
from game.house_rules import HouseRules
from game.guidance import (
    GuidanceState,
    build_action_mode_guidance,
    build_help_panel_content,
    build_side_panel_guidance,
)
from game.hex_tile import get_token_pip_count
from game.log_display import draw_log, draw_resource_counts
from game.lan_lobby_display import (
    ACTION_BACK,
    ACTION_CLOSE,
    ACTION_LEAVE_ROOM,
    ACTION_START_MATCH,
    LanLobbyDisplayState,
    draw_lan_lobby_display,
    hit_test_lan_lobby_display,
)
from game.lan_lobby_flow import LanLobbyFlow
from game.lan_match_display import (
    LanMatchDisplayState,
    draw_lan_match_display,
    hit_test_lan_match_display,
)
from game.match_metrics import MatchMetrics
from game.match_result import build_match_result
from game.persistence import (
    DEFAULT_SAVE_PATH,
    SaveGameError,
    load_game as load_game_file,
    restore_game as restore_game_state,
    save_game as save_game_file,
    serialize_game,
)
from game.player import Player
from game.pre_game_settings_display import (
    ACTION_APPLY as PRE_GAME_SETTINGS_APPLY,
    ACTION_CANCEL as PRE_GAME_SETTINGS_CANCEL,
    ACTION_EDIT_HARBORS,
    ACTION_EDIT_NUMBERS,
    ACTION_EDIT_TERRAIN,
    ACTION_RESET as PRE_GAME_SETTINGS_RESET,
    ACTION_SHUFFLE_HARBORS,
    ACTION_SHUFFLE_NUMBERS,
    ACTION_SHUFFLE_TERRAIN,
    ACTION_TAB_MAP,
    ACTION_TAB_RULES,
    ACTION_TOGGLE_BANK_3_TO_1,
    ACTION_TOGGLE_SKIP_DISCARD,
    PreGameSettingsDisplayState,
    development_toggle_action,
    draw_pre_game_settings_display,
    hit_test_pre_game_settings,
)
from game.resources import BUILD_COSTS, ResourceType
from game.resource_credit import (
    BANK_TO_PLAYER,
    LOAN_ACTIVE,
    PLAYER_TO_BANK,
    ResourceCreditError,
)
from game.road import Road
from game.trade_market import (
    MAX_OPEN_ORDERS,
    MAX_OPEN_ORDERS_PER_SELLER,
    MarketOrder,
    TradeMarketError,
)
from game.trade_auction import (
    LEDGER_CONSUME,
    LEDGER_RELEASE,
    LEDGER_REPLACE,
    LEDGER_RESERVE,
    MAX_OPEN_AUCTIONS,
    MAX_OPEN_AUCTIONS_PER_SELLER,
    AuctionLot,
    TradeAuctionError,
)
from game.variant import (
    COMPOSITE_VARIANT_KIND,
    CREDIT_VARIANT_KIND,
    TRADE2_AUCTION_CATALOG,
    TRADE2_VARIANT_KIND,
    VariantConfig,
    variant_board_topology,
    variant_uses_hidden_board,
)
from game.variant_state import VariantState, VariantStateError
from game.replay import (
    DEFAULT_REPLAY_DIR,
    ReplayError,
    ReplayRecorder,
    find_latest_replay,
    load_replay,
    restore_replay_frame,
)
from game.result_display import (
    NEW_BOARD_ACTION,
    REPLAY_SELECTED_ACTION,
    RESTART_SAME_BOARD_ACTION,
    build_result_display_layout,
    draw_result_display,
    hit_test_result_display,
    normalise_match_result,
    selected_replay_frame,
)
from game.ui import (
    RESOURCE_LABELS,
    PhaseStep,
    UIButton,
    draw_board_highlights,
    draw_help_panel,
    draw_ocean_background,
    draw_progress_header,
    draw_replay_status_card,
    draw_side_panel,
    draw_transient_message,
)


class _SilentAudio:
    """No-op audio backend used by deterministic headless simulations."""

    def start_bgm(self):
        return None

    def play(self, _name):
        return None

    def stop(self):
        return None


class _HeadlessDiceOverlay:
    """Minimal overlay contract for games that resolve dice immediately."""

    def __init__(self):
        self.state = "idle"

    @property
    def is_active(self):
        return False

    def update(self, _now):
        return False

    def draw(self, _screen):
        return None


MIXED_AI_PERSONALITIES = (EXPANSION, TRADER, DISRUPTOR)


class CatanGame:
    def __init__(
        self,
        board_mode="constrained",
        board_seed=None,
        *,
        custom_map=None,
        house_rules=None,
        variant_config=None,
        ai_player_count=0,
        ai_action_delay_ms=AI_ACTION_DELAY_MS,
        ai_personality_mode=STANDARD,
        headless=False,
    ):
        self.headless = bool(headless)
        if self.headless:
            self.screen = None
            self.clock = None
        else:
            pygame.init()
            self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
            pygame.display.set_caption(WINDOW_TITLE)
            self.clock = pygame.time.Clock()
        self.board_mode = "constrained" if board_mode == "balanced" else board_mode
        self.board_seed = self.normalize_board_seed(board_seed)
        self.board_seed_text = str(self.board_seed)
        self.seed_input_active = False
        self.victory_point_target = WINNING_VICTORY_POINTS
        self.house_rules = HouseRules.standard() if house_rules is None else house_rules
        if not isinstance(self.house_rules, HouseRules):
            raise TypeError("house_rules must be a HouseRules")
        self.variant_config = (
            VariantConfig.standard() if variant_config is None else variant_config
        )
        if not isinstance(self.variant_config, VariantConfig):
            raise TypeError("variant_config must be a VariantConfig")
        self.custom_map_spec = custom_map
        if self.board_mode == "custom":
            if not isinstance(self.custom_map_spec, CustomMapSpec):
                raise ValueError("custom board mode requires a CustomMapSpec")
        elif self.custom_map_spec is not None:
            raise ValueError("custom_map is only valid in custom board mode")
        if variant_uses_hidden_board(self.variant_config) and self.board_mode == "custom":
            raise ValueError("frontier variant is available only on generated boards")
        self.board = self.create_board_from_settings()
        self.board_rules = BoardRules(
            self.board,
            road_is_usable=self.is_road_usable,
        )
        self.variant_state = self.create_initial_variant_state()
        self.running = True
        self.audio = _SilentAudio() if self.headless else GameAudio()
        self.dice_overlay = (
            _HeadlessDiceOverlay() if self.headless else DiceAnimationOverlay()
        )
        self.feedback = FeedbackManager()
        self.bank = ResourceBank()
        self.ai = SimpleAI()
        self.ai_player_count = max(0, int(ai_player_count))
        self.ai_personality_mode = normalize_ai_personality_mode(ai_personality_mode)
        self._mixed_ai_personality_seed = secrets.randbits(128)
        self.ai_action_delay_ms = max(0, int(ai_action_delay_ms))
        timed_speed_indices = [
            index
            for index, (_, delay_ms) in enumerate(AI_SPEED_OPTIONS)
            if delay_ms is not None
        ]
        self.ai_speed_index = min(
            timed_speed_indices,
            key=lambda index: abs(AI_SPEED_OPTIONS[index][1] - self.ai_action_delay_ms),
        )
        self.ai_paused = False
        self.ai_next_action_at = 0
        self.ai_status = {
            "player_name": "",
            "title": "AI待機中",
            "detail": "AIの手番になると判断内容を表示します。",
        }
        self.last_resource_distribution = {}
        self.public_gain_history = {}
        self.match_metrics = MatchMetrics()
        self.match_result = None
        self.result_display_layout = None
        self.result_selected_event_index = 0
        self.lan_lobby_flow = None
        self.lan_lobby_layout = None
        self.lan_match_layout = None
        self.lan_selected_build_piece = None
        self.lan_match_visible = False
        self.lan_match_seen = False
        self.pre_game_settings_open = False
        self.pre_game_settings_layout = None
        self.pre_game_settings_tab = "map"
        self.pre_game_edit_layer = "terrain"
        self.pre_game_draft_map = None
        self.pre_game_draft_house_rules = None
        self.pre_game_draft_board_mode = None
        self.pre_game_selected_tile = None
        self.pre_game_selected_harbor = None
        self.pre_game_settings_error = ""
        if not self.headless:
            self.audio.start_bgm()

        self.player_palette = [
            ("Player1", COLORS["RED"]),
            ("Player2", COLORS["BLUE"]),
            ("Player3", COLORS["ORANGE"]),
            ("Player4", COLORS["CYAN"]),
        ]
        self.players = []
        self.turn_order = []
        self.buttons = []
        self.show_help_panel = False
        self.show_log_panel = False
        self.log_scroll_offset = 0
        self.quick_save_path = DEFAULT_SAVE_PATH
        self.replay_dir = DEFAULT_REPLAY_DIR
        self.replay_recorder = None
        self.replay_pending_capture = None
        self.replay_archive = None
        self.replay_index = 0
        self.replay_mode = False
        self.replay_playing = False
        self.replay_reveal_all = False
        self.replay_help_visible = False
        self.replay_next_frame_at = 0
        self.replay_exit_snapshot = None
        self.replay_runtime_state = None
        self.latest_replay_path = None

        self.phase = "initial"  # "initial", "main", "finished"
        self.initial_dice_phase = True
        self.initial_dice_results = {}
        self.initial_dice_histories = {}
        self.initial_dice_contenders = []
        self.initial_dice_pending_groups = []
        self.initial_placement_order = []
        self.initial_placement_counts = {}
        self.initial_round = 1
        self.initial_player_index = 0
        self.waiting_for_road = False
        self.last_settlement_node = None

        self.current_player_index = 0
        self.dice_rolled = False
        self.action_mode = None
        self.development_card_used_this_turn = False
        self.development_deck = create_development_deck(
            disabled_cards=self.house_rules.disabled_development_cards
        )
        self.special_phase = None
        self.discard_queue = []
        self.discard_player = None
        self.discard_remaining = 0
        self.robber_tile_candidates = []
        self.robber_target_players = []
        self.resource_selection_remaining = 0
        self.free_roads_remaining = 0
        self.bank_trade_give_resource = None
        self.winner = None
        self.longest_road_owner = None
        self.longest_road_length = 0
        self.largest_army_owner = None
        self.largest_army_size = 0

        self.log_messages = []
        self.latest_event = {
            "title": "ゲーム準備中",
            "detail": "人数・AI・勝利点・盤面を確認して初期ダイスを振ってください。",
            "level": "info",
            "color": COLORS["PANEL_BORDER"],
        }
        self.turn_summary_entries = []
        self.pending_dice_context = None
        self.pending_dice_roll = None
        self.pending_dice_player_name = ""
        # Keep the exact public d6 faces for LAN/Web presentation.  The total
        # alone is sufficient for rules resolution, but not for faithfully
        # reproducing the roll animation on another client.
        self.last_dice_pair = None
        self.configure_players(
            2,
            reset_logs=False,
            schedule_ai=not self.headless,
            reset_replay=not self.headless,
        )
        self.add_log("ゲーム開始: 初期配置フェーズです。")
        self.add_log("プレイヤー数は 2/3/4 キーまたは右のボタンで変更できます。")
        self.add_log(
            "人間プレイヤーはスペースキーで初期ダイスを振ります。AIは自動で進行します。"
        )
        if not self.headless:
            self.reset_replay_recording()
            self.refresh_latest_replay_path()

    def add_log(self, message):
        if self.headless:
            return
        if self.log_scroll_offset > 0:
            self.log_scroll_offset += 1
        self.log_messages.append(message)
        self.log_scroll_offset = min(
            self.log_scroll_offset,
            max(0, len(self.log_messages) - 1),
        )
        print(message)

    def record_event(
        self,
        title,
        detail="",
        *,
        level="info",
        actor=None,
        include_in_turn=True,
        capture_replay=True,
    ):
        color = actor.color if actor is not None else COLORS["PANEL_BORDER"]
        self.latest_event = {
            "title": title,
            "detail": detail,
            "level": level,
            "color": color,
        }
        if include_in_turn and self.phase == "main":
            summary = f"{title} — {detail}" if detail else title
            if summary and summary not in self.turn_summary_entries:
                self.turn_summary_entries.append(summary)
                self.turn_summary_entries = self.turn_summary_entries[-6:]
        if capture_replay and not self.replay_mode and self.replay_recorder is not None:
            # The actual snapshot is deferred until the action method returns.
            # Several flows set their special phase immediately after logging.
            self.replay_pending_capture = title
        elif capture_replay and not self.replay_mode:
            self.record_match_progress(title, None)

    def reset_match_metrics(self):
        """Start a clean, transport-safe statistics record for this match."""

        self.match_metrics = MatchMetrics()
        for index, player in enumerate(self.players, start=1):
            self.match_metrics.register_player(f"seat-{index}", player.name)
        self.match_result = None
        self.result_display_layout = None
        self.result_selected_event_index = 0

    def get_match_metric_player_id(self, player):
        """Return a stable seat identity that survives display-name changes."""

        try:
            return f"seat-{self.players.index(player) + 1}"
        except ValueError:
            return str(getattr(player, "name", player))

    def unlink_match_replay_frames(self):
        """Keep cumulative metrics while detaching links from an old recorder."""

        document = self.match_metrics.to_dict()
        display_names = {
            f"seat-{index}": player.name
            for index, player in enumerate(self.players, start=1)
        }
        for player_data in document.get("players", []):
            player_id = player_data.get("player_id")
            if player_id in display_names:
                player_data["display_name"] = display_names[player_id]
        for checkpoint in document.get("point_checkpoints", []):
            checkpoint["replay_frame_index"] = None
        for event in document.get("important_events", []):
            event["replay_frame_index"] = None
        self.match_metrics = MatchMetrics.from_dict(document)
        self.match_result = None
        self.result_display_layout = None

    def unlink_match_replay_frame(self, frame_index):
        """Detach metrics that pointed at a replay frame being replaced."""

        document = self.match_metrics.to_dict()
        changed = False
        for section in ("point_checkpoints", "important_events"):
            for item in document.get(section, []):
                if item.get("replay_frame_index") == frame_index:
                    item["replay_frame_index"] = None
                    changed = True
        if changed:
            self.match_metrics = MatchMetrics.from_dict(document)

    def record_match_progress(self, label, replay_frame_index):
        """Attach score changes and highlights to a stable replay frame."""

        if self.replay_mode or not self.players:
            return
        points = {
            self.get_match_metric_player_id(player): self.get_player_victory_points(
                player
            )
            for player in self.players
        }
        checkpoints = self.match_metrics.point_checkpoints
        points_changed = not checkpoints or checkpoints[-1].points != points
        detail = str(self.latest_event.get("detail", ""))
        if points_changed:
            self.match_metrics.record_point_checkpoint(
                str(label) or "得点更新",
                points,
                detail=detail,
                replay_frame_index=replay_frame_index,
            )

        important_terms = (
            "勝利",
            "開拓地",
            "都市",
            "交易成立",
            "騎士",
            "略奪",
            "盗賊",
            "イベント予告",
            "イベント発動",
            "発見",
            "借入",
            "返済",
            "延滞",
        )
        is_important = points_changed or any(
            term in str(label) for term in important_terms
        )
        if not is_important or str(label) == "対局準備":
            return
        events = self.match_metrics.important_events
        if (
            events
            and events[-1].replay_frame_index == replay_frame_index
            and events[-1].title == str(label)
        ):
            return
        self.match_metrics.record_important_event(
            str(label) or "重要イベント",
            detail,
            replay_frame_index=replay_frame_index,
        )

    def refresh_latest_replay_path(self):
        if self.headless:
            self.latest_replay_path = None
            return None
        try:
            self.latest_replay_path = find_latest_replay(self.replay_dir)
        except ReplayError:
            self.latest_replay_path = None
        return self.latest_replay_path

    def reset_replay_recording(self):
        """Start a fresh, bounded recording from the current pre-game state."""
        if self.headless or self.replay_mode:
            return False
        self.replay_recorder = ReplayRecorder(
            metadata={
                "title": "ローカル対局リプレイ",
                "visibility": "private-full-state",
            }
        )
        self.replay_pending_capture = None
        self.replay_archive = None
        try:
            frame = self.replay_recorder.capture(self, label="対局準備", elapsed_ms=0)
        except ReplayError:
            self.replay_recorder = None
            return False
        self.record_match_progress(frame.label, frame.sequence)
        return True

    def flush_replay_capture(self, *, force_latest=False):
        """Capture one stable semantic event, never a half-resolved dice roll."""
        if (
            self.replay_mode
            or self.replay_recorder is None
            or self.replay_pending_capture is None
            or self.has_active_dice_animation()
            or self.pending_dice_context is not None
            or self.pending_dice_roll is not None
        ):
            return False
        label = self.replay_pending_capture
        self.replay_pending_capture = None
        replacing_last = bool(
            force_latest
            and len(self.replay_recorder.frames) >= self.replay_recorder.max_frames
        )
        try:
            frame = self.replay_recorder.capture(
                self,
                label=label,
                replace_last_if_full=force_latest,
            )
        except ReplayError as exc:
            self.add_log(f"リプレイ記録を継続できません: {exc}")
            return False
        if replacing_last:
            self.unlink_match_replay_frame(frame.sequence)
        self.record_match_progress(frame.label, frame.sequence)
        return True

    def save_completed_replay(self):
        if self.replay_recorder is None:
            return None
        self.flush_replay_capture()
        try:
            self.replay_archive = self.replay_recorder.archive()
            path = self.replay_recorder.save(replay_dir=self.replay_dir)
        except ReplayError as exc:
            self.add_log(f"リプレイを保存できませんでした: {exc}")
            return None
        self.latest_replay_path = path
        self.add_log(f"リプレイを保存しました: {path.name}")
        return path

    def refresh_match_result(self):
        """Rebuild the public result payload used by desktop and future clients."""

        if self.phase != "finished":
            self.match_result = None
            self.result_display_layout = None
            return None
        self.match_result = build_match_result(self, replay=self.replay_archive)
        replay_frame_count = (
            len(self.replay_archive.frames) if self.replay_archive is not None else 0
        )
        self.match_result["replay"] = {
            "available": replay_frame_count > 0,
            "frame_count": replay_frame_count,
        }
        result = normalise_match_result(self.match_result)
        if result.important_events:
            self.result_selected_event_index = max(
                0,
                min(
                    self.result_selected_event_index,
                    len(result.important_events) - 1,
                ),
            )
        else:
            self.result_selected_event_index = 0
        self.result_display_layout = self.build_match_result_layout()
        return self.match_result

    def build_match_result_layout(self):
        if self.match_result is None or self.screen is None:
            return None
        result = normalise_match_result(self.match_result)
        replay_frame = selected_replay_frame(
            self.match_result,
            self.result_selected_event_index,
        )
        return build_result_display_layout(
            self.screen.get_size(),
            len(result.players),
            len(result.important_events),
            self.result_selected_event_index,
            replay_enabled=replay_frame is not None,
        )

    def move_result_selection(self, delta):
        if self.match_result is None:
            self.refresh_match_result()
        if self.match_result is None:
            return False
        events = normalise_match_result(self.match_result).important_events
        if not events:
            return False
        self.result_selected_event_index = max(
            0,
            min(
                self.result_selected_event_index + int(delta),
                len(events) - 1,
            ),
        )
        self.result_display_layout = self.build_match_result_layout()
        return True

    def handle_match_result_action(self, action):
        if action == REPLAY_SELECTED_ACTION:
            if self.match_result is None:
                self.refresh_match_result()
            frame_index = selected_replay_frame(
                self.match_result,
                self.result_selected_event_index,
            )
            if frame_index is None:
                self.notify_invalid(
                    "このイベントには対応するリプレイ位置がありません。"
                )
                return False
            archive = self.replay_archive
            if archive is None or not 0 <= frame_index < len(archive.frames):
                self.notify_invalid("この対局のリプレイ位置を確認できません。")
                return False
            if not self.start_replay(archive):
                return False
            return self.show_replay_frame(frame_index)
        if action == RESTART_SAME_BOARD_ACTION:
            self.restart_game(randomize_seed=False)
            return True
        if action == NEW_BOARD_ACTION:
            self.restart_game(randomize_seed=True)
            return True
        return False

    def get_current_replay_frame(self):
        if self.replay_archive is None or not self.replay_archive.frames:
            return None
        return self.replay_archive.frames[self.replay_index]

    def show_replay_frame(self, index):
        if not self.replay_mode or self.replay_archive is None:
            return False
        index = max(0, min(int(index), len(self.replay_archive.frames) - 1))
        try:
            restore_replay_frame(
                self,
                self.replay_archive,
                index,
                validate_archive=False,
            )
        except ReplayError as exc:
            self.replay_playing = False
            self.notify_invalid(str(exc))
            return False
        self.replay_index = index
        self.show_log_panel = False
        self.show_help_panel = self.replay_help_visible
        self.log_scroll_offset = 0
        self.feedback.clear()
        self.buttons = [] if self.headless else self.build_buttons()
        return True

    def start_replay(self, archive=None):
        """Open a completed match as a read-only viewer."""
        if self.replay_mode:
            return True
        if not (
            self.phase == "finished"
            or (self.phase == "initial" and self.initial_dice_phase)
        ):
            self.notify_invalid("リプレイは対局終了後か開始前に開けます。")
            return False

        try:
            if archive is None:
                if self.phase == "finished" and self.replay_archive is not None:
                    archive = self.replay_archive
                else:
                    replay_path = self.refresh_latest_replay_path()
                    if replay_path is None:
                        raise ReplayError("保存済みのリプレイがありません。")
                    # Structural limits are checked up front.  Each frame gets
                    # full semantic validation when it is shown, avoiding a
                    # long startup pause on large replays.
                    archive = load_replay(replay_path)
            origin = serialize_game(self)
        except (ReplayError, SaveGameError) as exc:
            self.notify_invalid(str(exc))
            return False

        now = pygame.time.get_ticks()
        self.replay_exit_snapshot = origin
        self.replay_runtime_state = {
            "random_state": random.getstate(),
            "ai_remaining_ms": max(0, self.ai_next_action_at - now),
        }
        self.replay_archive = archive
        self.replay_index = 0
        self.replay_playing = False
        self.replay_reveal_all = False
        self.replay_help_visible = False
        self.replay_mode = True
        if not self.show_replay_frame(0):
            self.replay_mode = False
            self.replay_archive = None
            self.replay_exit_snapshot = None
            self.replay_runtime_state = None
            try:
                restore_game_state(self, origin, runtime_side_effects=False)
            except SaveGameError:
                pass
            return False
        return True

    def exit_replay(self):
        if not self.replay_mode or self.replay_exit_snapshot is None:
            return False
        origin = self.replay_exit_snapshot
        runtime_state = self.replay_runtime_state or {}
        self.replay_playing = False
        self.replay_mode = False
        try:
            restore_game_state(self, origin, runtime_side_effects=False)
        except SaveGameError as exc:
            self.replay_mode = True
            self.notify_invalid(f"リプレイ終了時の復元に失敗しました: {exc}")
            return False
        random_state = runtime_state.get("random_state")
        if random_state is not None:
            random.setstate(random_state)
        self.ai_next_action_at = pygame.time.get_ticks() + int(
            runtime_state.get("ai_remaining_ms", 0)
        )
        self.replay_exit_snapshot = None
        self.replay_runtime_state = None
        self.replay_reveal_all = False
        self.replay_help_visible = False
        self.buttons = self.build_buttons()
        return True

    def toggle_replay_playback(self):
        if not self.replay_mode or self.replay_archive is None:
            return False
        if (
            not self.replay_playing
            and self.replay_index >= len(self.replay_archive.frames) - 1
        ):
            self.show_replay_frame(0)
        self.replay_playing = not self.replay_playing
        self.replay_next_frame_at = pygame.time.get_ticks() + 850
        return True

    def handle_replay_action(self, action):
        if action == "replay_first":
            self.replay_playing = False
            return self.show_replay_frame(0)
        if action == "replay_previous":
            self.replay_playing = False
            return self.show_replay_frame(self.replay_index - 1)
        if action == "replay_play_pause":
            return self.toggle_replay_playback()
        if action == "replay_next":
            self.replay_playing = False
            return self.show_replay_frame(self.replay_index + 1)
        if action == "replay_last":
            self.replay_playing = False
            return self.show_replay_frame(len(self.replay_archive.frames) - 1)
        if action == "replay_toggle_reveal":
            self.replay_reveal_all = not self.replay_reveal_all
            return True
        if action == "replay_exit":
            return self.exit_replay()
        return False

    def handle_replay_event(self, event):
        if event.type == pygame.KEYDOWN:
            key_actions = {
                pygame.K_HOME: "replay_first",
                pygame.K_LEFT: "replay_previous",
                pygame.K_SPACE: "replay_play_pause",
                pygame.K_RIGHT: "replay_next",
                pygame.K_END: "replay_last",
                pygame.K_v: "replay_toggle_reveal",
                pygame.K_ESCAPE: "replay_exit",
            }
            if event.key == pygame.K_h:
                self.replay_help_visible = not self.replay_help_visible
                self.show_help_panel = self.replay_help_visible
                return True
            action = key_actions.get(event.key)
            if action is not None:
                self.handle_replay_action(action)
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            clicked_button = self.find_clicked_button(event.pos)
            if clicked_button is not None:
                self.handle_replay_action(clicked_button.action)
            return True
        return event.type in (pygame.MOUSEWHEEL, pygame.TEXTINPUT)

    def update_replay(self):
        if (
            not self.replay_mode
            or not self.replay_playing
            or self.replay_archive is None
        ):
            return
        now = pygame.time.get_ticks()
        if now < self.replay_next_frame_at:
            return
        if self.replay_index >= len(self.replay_archive.frames) - 1:
            self.replay_playing = False
            return
        self.show_replay_frame(self.replay_index + 1)
        self.replay_next_frame_at = now + 850

    def play_sound(self, sound_name):
        self.audio.play(sound_name)

    def schedule_ai_action(self, delay_multiplier=1.0):
        delay = int(self.ai_action_delay_ms * max(0.0, delay_multiplier))
        self.ai_next_action_at = pygame.time.get_ticks() + delay

    def get_ai_speed_label(self):
        return AI_SPEED_OPTIONS[self.ai_speed_index][0]

    def get_ai_speed_compact_label(self):
        return {
            "ゆっくり": "低速",
            "標準": "標準",
            "高速": "高速",
            "一時停止": "停止",
        }[self.get_ai_speed_label()]

    def get_ai_personality_mode_label(self):
        if self.ai_personality_mode == MIXED:
            return "混合"
        return AI_PERSONALITY_PROFILES[
            normalize_ai_personality(self.ai_personality_mode)
        ].label

    def get_ai_personality_mode_compact_label(self):
        labels = {
            STANDARD: "標準",
            MIXED: "混合",
            EXPANSION: "拡大",
            TRADER: "交渉",
            DISRUPTOR: "妨害",
        }
        return labels[self.ai_personality_mode]

    def get_player_ai_personality_label(self, player):
        if player is None or not player.is_ai:
            return ""
        return AI_PERSONALITY_PROFILES[
            normalize_ai_personality(getattr(player, "ai_personality", STANDARD))
        ].label

    def get_public_player_ai_personality_label(self, player):
        """Return the in-match label without revealing a mixed AI profile."""
        if player is None or not player.is_ai:
            return ""
        if self.ai_personality_mode == MIXED:
            return "性格非公開"
        return self.get_player_ai_personality_label(player)

    def assign_ai_personalities(self):
        """Apply the selected pre-game personality mode without rebuilding seats."""
        mixed_personalities = list(MIXED_AI_PERSONALITIES)
        random.Random(self._mixed_ai_personality_seed).shuffle(mixed_personalities)
        cpu_index = 0
        for player in self.players:
            if not player.is_ai:
                player.ai_personality = STANDARD
                continue
            if self.ai_personality_mode == MIXED:
                personality = mixed_personalities[
                    cpu_index % len(mixed_personalities)
                ]
            else:
                personality = normalize_ai_personality(self.ai_personality_mode)
            player.ai_personality = personality
            cpu_index += 1

    def set_ai_personality_mode(self, mode):
        if not self.can_edit_pre_game_settings():
            self.notify_invalid("AI性格は初期ダイスを振る前だけ変更できます。")
            return False
        if mode not in AI_PERSONALITY_MODES or mode == self.ai_personality_mode:
            return False
        self.ai_personality_mode = mode
        self.assign_ai_personalities()
        self.add_log(
            f"AI性格を「{self.get_ai_personality_mode_label()}」に変更しました。"
        )
        self.reset_replay_recording()
        return True

    def cycle_ai_personality_mode(self):
        current_index = AI_PERSONALITY_MODES.index(self.ai_personality_mode)
        next_mode = AI_PERSONALITY_MODES[
            (current_index + 1) % len(AI_PERSONALITY_MODES)
        ]
        return self.set_ai_personality_mode(next_mode)

    def cycle_ai_speed(self):
        reset_replay = self.can_edit_pre_game_settings()
        self.ai_speed_index = (self.ai_speed_index + 1) % len(AI_SPEED_OPTIONS)
        _, delay_ms = AI_SPEED_OPTIONS[self.ai_speed_index]
        self.ai_paused = delay_ms is None
        if delay_ms is not None:
            self.ai_action_delay_ms = delay_ms
            self.schedule_ai_action(0.45)
        self.add_log(f"AI速度を「{self.get_ai_speed_label()}」に変更しました。")
        if reset_replay:
            self.reset_replay_recording()

    def set_ai_status(self, player, title, detail="", *, log=False):
        if player is None or not player.is_ai:
            return
        previous = (self.ai_status.get("player_name"), self.ai_status.get("title"))
        self.ai_status = {
            "player_name": player.name,
            "personality": normalize_ai_personality(player.ai_personality),
            "title": title,
            "detail": detail,
        }
        if log and previous != (player.name, title):
            suffix = f" — {detail}" if detail else ""
            personality_label = self.get_public_player_ai_personality_label(player)
            self.add_log(f"{player.name}（{personality_label}）の判断: {title}{suffix}")

    def record_public_gain(self, player, bundle, source):
        if player is None:
            return
        gain_text = self.format_resource_bundle(bundle)
        if gain_text == "なし":
            return
        history = self.public_gain_history.setdefault(player.name, [])
        history.append({"source": source, "text": gain_text})
        self.public_gain_history[player.name] = history[-4:]

    def get_recent_public_gain_text(self, player):
        if player is None:
            return "なし"
        history = self.public_gain_history.get(player.name, [])
        if not history:
            return "なし"
        latest = history[-1]
        return f"{latest['text']}（{latest['source']}）"

    def get_public_production_profile(self, player, limit=2):
        if player is None:
            return "なし"
        scores = {resource_type: 0 for resource_type in RESOURCE_TYPES}
        for node in self.board.nodes:
            if node.building is None or node.building.owner is not player:
                continue
            multiplier = node.building.resource_multiplier
            for tile in self.get_public_node_tiles(node):
                if tile.resource_type == ResourceType.DESERT:
                    continue
                scores[tile.resource_type] += (
                    get_token_pip_count(tile.number) * multiplier
                )
        ranked = [
            resource_type
            for resource_type, score in sorted(
                scores.items(),
                key=lambda item: (-item[1], item[0].value),
            )
            if score > 0
        ]
        if not ranked:
            return "なし"
        return "・".join(
            RESOURCE_LABELS[resource_type] for resource_type in ranked[:limit]
        )

    def get_trade_partner_public_summary(self, partner):
        profile = self.get_public_production_profile(partner)
        recent = self.get_recent_public_gain_text(partner)
        return (
            f"手札{partner.total_resource_count()}枚 / 生産{profile} / 直近獲得{recent}"
        )

    def generate_board_seed(self):
        return random.randint(10000, 99999999)

    def normalize_board_seed(self, value):
        if value is None:
            return self.generate_board_seed()
        return int(value)

    def create_board_from_settings(self):
        """Build the board described by the portable pre-game settings."""

        topology_id = variant_board_topology(self.variant_config)
        return GameBoard(
            mode=self.board_mode,
            seed=self.board_seed,
            custom_map=(self.custom_map_spec if self.board_mode == "custom" else None),
            topology_id=topology_id,
        )

    def create_initial_variant_state(self):
        """Bind fresh variant state to the board that owns hidden information."""

        robber_axial = None
        if variant_uses_hidden_board(self.variant_config):
            if self.board_mode == "custom":
                raise ValueError("frontier variant is available only on generated boards")
            if self.board.robber_tile is None:
                raise ValueError("frontier variant requires a robber tile")
            robber_axial = self.board.robber_tile.axial
        forecast_harbor_ids = None
        forecast_config = self.variant_config.component_config(
            FORECAST_EVENTS_KIND
        )
        if (
            forecast_config is not None
            and forecast_config.options.get("catalog")
            == CAMPAIGN_FORECAST_CATALOG_ID
        ):
            frontier_config = self.variant_config.component_config(FRONTIER_KIND)
            if frontier_config is None or robber_axial is None:
                raise ValueError("campaign forecast requires frontier state")
            initial_frontier = VariantState.initial(
                frontier_config,
                frontier_robber_axial=robber_axial,
            )
            forecast_harbor_ids = self._public_harbor_ids_for_frontier_state(
                initial_frontier
            )
        return VariantState.initial(
            self.variant_config,
            frontier_robber_axial=robber_axial,
            forecast_harbor_ids=forecast_harbor_ids,
        )

    def _public_harbor_ids_for_frontier_state(self, frontier_state):
        """Return stable IDs visible in one supplied frontier snapshot."""

        from game.network_protocol import build_board_reference_index

        harbor_references = build_board_reference_index(self)["harbor"]
        visible = []
        for harbor_id, harbor in harbor_references.items():
            adjacent = self.board.get_edge_adjacent_tiles(
                (harbor.node1, harbor.node2)
            )
            if any(
                frontier_state.is_frontier_tile_revealed(tile.axial)
                for tile in adjacent
            ):
                visible.append(harbor_id)
        return tuple(visible)

    def get_public_harbor_ids(self):
        """Return current viewer-safe stable harbor IDs in canonical order."""

        if not self.is_frontier_variant():
            return None
        frontier_state = self.get_variant_component_state(FRONTIER_KIND)
        if frontier_state is None:
            return None
        return self._public_harbor_ids_for_frontier_state(frontier_state)

    def get_campaign_forecast_harbor_ids(self):
        forecast_config = self.get_variant_component_config(FORECAST_EVENTS_KIND)
        if (
            forecast_config is None
            or forecast_config.options.get("catalog")
            != CAMPAIGN_FORECAST_CATALOG_ID
        ):
            return None
        harbor_ids = self.get_public_harbor_ids()
        return () if harbor_ids is None else harbor_ids

    def get_active_feedback(self):
        return self.feedback.get_active(pygame.time.get_ticks())

    def notify(self, message, *, level="info", log=True, transient=True):
        if log:
            self.add_log(message)
        if transient:
            self.feedback.show(message, level=level, now_ms=pygame.time.get_ticks())

    def notify_invalid(self, message):
        self.notify(message, level="error")

    def clear_log(self):
        self.log_messages = []
        self.log_scroll_offset = 0

    def quick_save(self):
        try:
            path = save_game_file(self, self.quick_save_path)
        except SaveGameError as exc:
            self.notify_invalid(str(exc))
            return False
        self.record_event(
            "クイックセーブ完了",
            path.name,
            level="success",
            include_in_turn=False,
            capture_replay=False,
        )
        self.notify(f"ゲームを保存しました: {path.name}", level="success")
        return True

    def quick_load(self):
        try:
            path = load_game_file(self, self.quick_save_path)
        except SaveGameError as exc:
            self.notify_invalid(str(exc))
            return False
        self.log_scroll_offset = 0
        self.unlink_match_replay_frames()
        self.reset_replay_recording()
        self.record_event(
            "クイックロード完了",
            path.name,
            level="success",
            include_in_turn=False,
            capture_replay=False,
        )
        self.notify(f"ゲームを読み込みました: {path.name}", level="success")
        return True

    def toggle_log_panel(self):
        self.show_log_panel = not self.show_log_panel
        if not self.show_log_panel:
            self.log_scroll_offset = 0

    def scroll_log(self, amount):
        if not self.log_messages:
            self.log_scroll_offset = 0
            return
        self.log_scroll_offset = max(
            0,
            min(
                self.log_scroll_offset + int(amount),
                len(self.log_messages) - 1,
            ),
        )

    def handle_global_ui_event(self, event):
        if self.replay_mode:
            return self.handle_replay_event(event)
        if event.type == pygame.KEYDOWN and event.key == pygame.K_F5:
            self.quick_save()
            return True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_F9:
            self.quick_load()
            return True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_l:
            self.toggle_log_panel()
            return True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_h:
            self.show_help_panel = not self.show_help_panel
            return True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_a:
            self.cycle_ai_speed()
            return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            clicked_button = self.find_clicked_button(event.pos)
            if clicked_button is not None and clicked_button.action == "ai_speed_cycle":
                self.handle_button_action(clicked_button.action)
                return True

        if self.show_log_panel and event.type == pygame.MOUSEWHEEL:
            self.scroll_log(event.y * 3)
            return True
        if self.show_log_panel and event.type == pygame.KEYDOWN:
            if event.key == pygame.K_PAGEUP:
                self.scroll_log(10)
                return True
            if event.key == pygame.K_PAGEDOWN:
                self.scroll_log(-10)
                return True
            if event.key == pygame.K_HOME:
                self.log_scroll_offset = max(0, len(self.log_messages) - 1)
                return True
            if event.key == pygame.K_END:
                self.log_scroll_offset = 0
                return True

        log_header_rect = pygame.Rect(12, 12, LOG_PANEL_WIDTH, 50)
        if (
            event.type == pygame.MOUSEBUTTONDOWN
            and event.button == 1
            and log_header_rect.collidepoint(event.pos)
        ):
            self.toggle_log_panel()
            return True
        return False

    def get_board_configuration_summary(self):
        mode_label = {
            "constrained": "制約付き",
            "fully_random": "公式ランダム",
            "custom": "カスタム",
        }.get(self.board_mode, self.board_mode)
        ai_label = (
            self.get_ai_personality_mode_label() if self.ai_player_count else "なし"
        )
        variant_label = {
            FORECAST_EVENTS_KIND: "予告イベント",
            FRONTIER_KIND: "フロンティア探索",
            TRADE2_VARIANT_KIND: (
                "交易2.0・市場と公開競売"
                if self.is_trade_auction_variant()
                else "交易2.0・常設市場"
            ),
            CREDIT_VARIANT_KIND: "資源信用所",
            COMPOSITE_VARIANT_KIND: "複合・予告イベント＋交易2.0＋信用",
        }.get(self.variant_config.kind, "通常")
        return (
            f"{mode_label} / seed {self.board_seed} / AI {self.ai_player_count}人（{ai_label}）/ "
            f"勝利{self.victory_point_target}点 / モード {variant_label} / "
            f"追加ルール {self.house_rules.compact_label()}"
        )

    def get_board_rules(self):
        board_rules = getattr(self, "board_rules", None)
        if board_rules is None:
            self.board_rules = BoardRules(
                self.board,
                road_is_usable=self.is_road_usable,
            )
            return self.board_rules
        if board_rules.board is not self.board:
            board_rules.set_board(self.board)
        board_rules.set_road_is_usable(self.is_road_usable)
        return board_rules

    def get_pre_game_board_summary(self):
        compact_mode_label = {
            "constrained": "制約",
            "fully_random": "公式",
            "custom": "カスタム",
        }.get(self.board_mode, self.board_mode)
        if self.board_mode == "constrained":
            description = "6/8隣接と数字・港の偏りを抑制。"
        elif self.board_mode == "custom":
            description = f"編集盤面 / 追加ルール: {self.house_rules.compact_label()}"
        else:
            description = "公式条件で資源・数字・港をシャッフル。"

        accent = "seed欄をクリックで編集"
        if not self.can_edit_pre_game_settings():
            accent = "初期ダイス開始後は設定を固定"
        elif self.seed_input_active:
            accent = "入力中: Enterで反映 / Escで終了"

        return {
            "title": "盤面サマリー",
            "rows": [
                ("盤面", f"{compact_mode_label} / seed {self.board_seed}"),
                (
                    "AI",
                    (
                        f"{self.ai_player_count}人 / "
                        f"{self.get_ai_personality_mode_label()}"
                    )
                    if self.ai_player_count
                    else "なし",
                ),
                ("勝利条件", f"{self.victory_point_target} VP"),
                (
                    "モード",
                    {
                        FORECAST_EVENTS_KIND: "予告イベント",
                        FRONTIER_KIND: "フロンティア探索",
                        TRADE2_VARIANT_KIND: (
                            "交易2.0・市場と公開競売"
                            if self.is_trade_auction_variant()
                            else "交易2.0・常設市場"
                        ),
                        CREDIT_VARIANT_KIND: "資源信用所",
                        COMPOSITE_VARIANT_KIND: "複合・イベント経済",
                    }.get(self.variant_config.kind, "通常"),
                ),
            ],
            "description": description,
            "accent": accent,
        }

    def can_edit_pre_game_settings(self):
        return bool(
            self.phase == "initial"
            and self.initial_dice_phase
            and not any(self.initial_dice_histories.values())
            and self.pending_dice_context is None
            and self.pending_dice_roll is None
            and not self.has_active_dice_animation()
        )

    def is_pre_game_settings_open(self):
        return bool(self.pre_game_settings_open)

    def open_pre_game_settings(self):
        """Open a transactional editor over the current pre-game settings."""

        if not self.can_edit_pre_game_settings():
            self.notify_invalid("詳細設定は初期ダイスを振る前だけ変更できます。")
            return False
        map_name = (
            self.custom_map_spec.name
            if self.custom_map_spec is not None
            else "カスタムマップ"
        )
        try:
            self.pre_game_draft_map = CustomMapSpec.from_board(
                self.board,
                name=map_name,
            )
        except CustomMapError as exc:
            self.notify_invalid(str(exc))
            return False
        self.pre_game_draft_house_rules = self.house_rules
        self.pre_game_draft_board_mode = self.board_mode
        self.pre_game_settings_tab = "map"
        self.pre_game_edit_layer = "terrain"
        self.pre_game_selected_tile = None
        self.pre_game_selected_harbor = None
        self.pre_game_settings_error = ""
        self.pre_game_settings_layout = None
        self.pre_game_settings_open = True
        self.seed_input_active = False
        self.buttons = []
        return True

    def close_pre_game_settings(self):
        if not self.pre_game_settings_open:
            return False
        self.pre_game_settings_open = False
        self.pre_game_settings_layout = None
        self.pre_game_draft_map = None
        self.pre_game_draft_house_rules = None
        self.pre_game_draft_board_mode = None
        self.pre_game_selected_tile = None
        self.pre_game_selected_harbor = None
        self.pre_game_settings_error = ""
        return True

    def get_pre_game_settings_display_state(self):
        if self.pre_game_draft_map is None or self.pre_game_draft_house_rules is None:
            raise RuntimeError("詳細設定のdraftがありません。")
        return PreGameSettingsDisplayState(
            map_spec=self.pre_game_draft_map,
            house_rules=self.pre_game_draft_house_rules,
            tab=self.pre_game_settings_tab,
            edit_layer=self.pre_game_edit_layer,
            selected_tile=self.pre_game_selected_tile,
            selected_harbor=self.pre_game_selected_harbor,
            can_apply=True,
            error=self.pre_game_settings_error,
        )

    def refresh_pre_game_map_warning(self):
        if self.pre_game_draft_map is None:
            self.pre_game_settings_error = ""
            return
        warnings = self.pre_game_draft_map.balance_warnings()
        self.pre_game_settings_error = (
            "注意: " + " / ".join(warnings[:2]) if warnings else ""
        )

    def mark_pre_game_map_custom(self, map_spec):
        if not isinstance(map_spec, CustomMapSpec):
            raise TypeError("map_spec must be a CustomMapSpec")
        self.pre_game_draft_map = map_spec
        self.pre_game_draft_board_mode = "custom"
        self.pre_game_selected_tile = None
        self.pre_game_selected_harbor = None
        self.refresh_pre_game_map_warning()

    def reset_pre_game_settings_draft(self):
        generated = GameBoard(mode="constrained", seed=self.board_seed)
        self.pre_game_draft_map = CustomMapSpec.from_board(
            generated,
            name="カスタムマップ",
        )
        self.pre_game_draft_house_rules = HouseRules.standard()
        self.pre_game_draft_board_mode = "constrained"
        self.pre_game_selected_tile = None
        self.pre_game_selected_harbor = None
        self.pre_game_settings_error = (
            "公式設定へ戻しました。適用するまで対局には反映されません。"
        )

    def apply_pre_game_settings_draft(self):
        if not self.pre_game_settings_open or not self.can_edit_pre_game_settings():
            return False
        if (
            self.pre_game_draft_map is None
            or self.pre_game_draft_house_rules is None
            or self.pre_game_draft_board_mode
            not in (
                "constrained",
                "fully_random",
                "custom",
            )
        ):
            self.pre_game_settings_error = "設定内容が不完全です。"
            return False

        draft_mode = self.pre_game_draft_board_mode
        draft_map = self.pre_game_draft_map if draft_mode == "custom" else None
        # Validate and build before mutating the live match.  This keeps Cancel
        # and failed Apply operations genuinely transactional.
        try:
            candidate_board = GameBoard(
                mode=draft_mode,
                seed=self.board_seed,
                custom_map=draft_map,
            )
        except (CustomMapError, TypeError, ValueError) as exc:
            self.pre_game_settings_error = str(exc)
            return False

        self.board_mode = draft_mode
        self.custom_map_spec = draft_map
        self.house_rules = self.pre_game_draft_house_rules
        player_count = len(self.players) or 2
        self.board = candidate_board
        self.get_board_rules().set_board(self.board)
        self.pre_game_settings_open = False
        self.pre_game_settings_layout = None
        self.configure_players(player_count, reset_logs=False)
        self.feedback.clear()
        self.clear_log()
        self.add_log(f"詳細設定を適用: {self.get_board_configuration_summary()}")
        self.add_log("人数を決めて初期ダイスを開始してください。")
        self.pre_game_draft_map = None
        self.pre_game_draft_house_rules = None
        self.pre_game_draft_board_mode = None
        self.pre_game_selected_tile = None
        self.pre_game_selected_harbor = None
        self.pre_game_settings_error = ""
        return True

    def handle_pre_game_settings_action(self, action):
        if not self.pre_game_settings_open:
            return False
        if action == PRE_GAME_SETTINGS_CANCEL:
            return self.close_pre_game_settings()
        if action == PRE_GAME_SETTINGS_APPLY:
            return self.apply_pre_game_settings_draft()
        if action == PRE_GAME_SETTINGS_RESET:
            self.reset_pre_game_settings_draft()
            return True
        if action == ACTION_TAB_MAP:
            self.pre_game_settings_tab = "map"
            return True
        if action == ACTION_TAB_RULES:
            self.pre_game_settings_tab = "rules"
            self.pre_game_selected_tile = None
            self.pre_game_selected_harbor = None
            return True
        layer_by_action = {
            ACTION_EDIT_TERRAIN: "terrain",
            ACTION_EDIT_NUMBERS: "numbers",
            ACTION_EDIT_HARBORS: "harbors",
        }
        if action in layer_by_action:
            self.pre_game_edit_layer = layer_by_action[action]
            self.pre_game_selected_tile = None
            self.pre_game_selected_harbor = None
            self.pre_game_settings_error = ""
            return True
        if self.pre_game_draft_map is not None:
            shuffle_by_action = {
                ACTION_SHUFFLE_TERRAIN: "shuffle_tiles",
                ACTION_SHUFFLE_NUMBERS: "shuffle_numbers",
                ACTION_SHUFFLE_HARBORS: "shuffle_harbors",
            }
            method_name = shuffle_by_action.get(action)
            if method_name is not None:
                self.mark_pre_game_map_custom(
                    getattr(self.pre_game_draft_map, method_name)()
                )
                return True
        if self.pre_game_draft_house_rules is not None:
            rules = self.pre_game_draft_house_rules
            if action == ACTION_TOGGLE_BANK_3_TO_1:
                self.pre_game_draft_house_rules = replace(
                    rules,
                    bank_trade_3_to_1=not rules.bank_trade_3_to_1,
                )
                return True
            if action == ACTION_TOGGLE_SKIP_DISCARD:
                self.pre_game_draft_house_rules = replace(
                    rules,
                    skip_discard_on_seven=not rules.skip_discard_on_seven,
                )
                return True
            for card_type in DevelopmentCardType:
                if action == development_toggle_action(card_type):
                    self.pre_game_draft_house_rules = rules.toggle_development_card(
                        card_type
                    )
                    return True
        return False

    def handle_pre_game_tile_target(self, axial):
        if self.pre_game_draft_map is None or self.pre_game_edit_layer == "harbors":
            return False
        coordinate = tuple(axial)
        if self.pre_game_edit_layer == "numbers":
            try:
                selected = self.pre_game_draft_map.tile_at(coordinate)
            except CustomMapError as exc:
                self.pre_game_settings_error = str(exc)
                return False
            if selected.resource is ResourceType.DESERT:
                self.pre_game_settings_error = (
                    "砂漠には数字チップがないため選択できません。"
                )
                return False
        if self.pre_game_selected_tile is None:
            self.pre_game_selected_tile = coordinate
            self.pre_game_settings_error = "もう1枚選ぶと入れ替えます。"
            return True
        first = self.pre_game_selected_tile
        if first == coordinate:
            self.pre_game_selected_tile = None
            self.pre_game_settings_error = "選択を解除しました。"
            return True
        try:
            if self.pre_game_edit_layer == "numbers":
                updated = self.pre_game_draft_map.swap_numbers(first, coordinate)
            else:
                updated = self.pre_game_draft_map.swap_tiles(first, coordinate)
        except CustomMapError as exc:
            self.pre_game_settings_error = str(exc)
            return False
        self.mark_pre_game_map_custom(updated)
        return True

    def handle_pre_game_harbor_target(self, harbor_index):
        if self.pre_game_draft_map is None or self.pre_game_edit_layer != "harbors":
            return False
        if self.pre_game_selected_harbor is None:
            self.pre_game_selected_harbor = int(harbor_index)
            self.pre_game_settings_error = "もう1つ港を選ぶと入れ替えます。"
            return True
        first = self.pre_game_selected_harbor
        if first == harbor_index:
            self.pre_game_selected_harbor = None
            self.pre_game_settings_error = "選択を解除しました。"
            return True
        try:
            updated = self.pre_game_draft_map.swap_harbors(
                first,
                int(harbor_index),
            )
        except CustomMapError as exc:
            self.pre_game_settings_error = str(exc)
            return False
        self.mark_pre_game_map_custom(updated)
        return True

    def handle_pre_game_settings_event(self, event):
        if not self.pre_game_settings_open:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.close_pre_game_settings()
                return True
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.apply_pre_game_settings_draft()
                return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.pre_game_settings_layout is None:
                return True
            target = hit_test_pre_game_settings(
                self.pre_game_settings_layout,
                event.pos,
            )
            if target is None:
                return True
            if target.kind == "action":
                self.handle_pre_game_settings_action(target.action)
            elif target.kind == "tile":
                self.handle_pre_game_tile_target(target.axial)
            elif target.kind == "harbor":
                self.handle_pre_game_harbor_target(target.harbor_index)
            return True
        return True

    def get_lan_room_settings(self):
        """Return the current pre-game settings accepted by the LAN host."""

        settings = {
            "player_count": len(self.players),
            "victory_target": self.victory_point_target,
            "board_mode": self.board_mode,
            "board_seed": self.board_seed,
            "variant": self.variant_config.to_document(),
        }
        if self.board_mode == "custom":
            if not isinstance(self.custom_map_spec, CustomMapSpec):
                raise ValueError("カスタム盤面の設定がありません。")
            settings["custom_map"] = self.custom_map_spec.to_document()
        if self.house_rules != HouseRules.standard():
            settings["house_rules"] = self.house_rules.to_document()
        return settings

    @staticmethod
    def copy_lan_room_code(text):
        """Copy an ASCII room code through Pygame's platform clipboard."""

        if not isinstance(text, str) or not text:
            raise ValueError("コピーする参加コードがありません。")
        pygame.scrap.init()
        pygame.scrap.put(pygame.SCRAP_TEXT, text.encode("utf-8") + b"\0")

    def open_lan_lobby(self):
        if self.headless:
            self.notify_invalid("ヘッドレス実行ではLANロビーを開けません。")
            return False
        if not self.can_edit_pre_game_settings():
            self.notify_invalid("LAN対戦は初期ダイスを振る前に開始してください。")
            return False
        if self.lan_lobby_flow is None:
            default_name = self.players[0].name if self.players else "Player"
            self.lan_lobby_flow = LanLobbyFlow(
                room_settings_provider=self.get_lan_room_settings,
                clipboard_callback=self.copy_lan_room_code,
                default_name=default_name,
                default_address="0.0.0.0:47624",
            )
        self.lan_lobby_flow.open()
        self.seed_input_active = False
        self.lan_lobby_layout = None
        self.lan_match_layout = None
        self.lan_selected_build_piece = None
        self.lan_match_visible = False
        self.lan_match_seen = False
        self.buttons = []
        return True

    def close_lan_lobby(self, *, permanent=False):
        flow = self.lan_lobby_flow
        if flow is None:
            return False
        if permanent:
            flow.close()
            self.lan_lobby_flow = None
        else:
            flow.leave_room(close_overlay=True)
        self.lan_lobby_layout = None
        self.lan_match_layout = None
        self.lan_selected_build_piece = None
        self.lan_match_visible = False
        self.lan_match_seen = False
        return True

    def is_lan_overlay_open(self):
        return bool(self.lan_lobby_flow and self.lan_lobby_flow.is_open)

    def get_lan_lobby_display_state(self):
        if self.lan_lobby_flow is None:
            raise RuntimeError("LANロビーが初期化されていません。")
        return LanLobbyDisplayState(**asdict(self.lan_lobby_flow.display_state))

    def sync_lan_build_selection(self):
        flow = self.lan_lobby_flow
        if flow is None:
            self.lan_selected_build_piece = None
            return
        available = {
            option.get("args", {}).get("piece")
            for option in flow.latest_command_options
            if option.get("command") == "build"
            and isinstance(option.get("args"), Mapping)
        }
        if self.lan_selected_build_piece not in available:
            self.lan_selected_build_piece = None

    def get_lan_match_display_state(self):
        flow = self.lan_lobby_flow
        if flow is None or flow.latest_game_view is None:
            return None
        self.sync_lan_build_selection()
        flow_state = flow.display_state
        return LanMatchDisplayState(
            view=flow.latest_game_view,
            command_options=(
                () if flow.command_pending else flow.latest_command_options
            ),
            selected_build_piece=self.lan_selected_build_piece,
            room_code=flow_state.room_code,
            connected=flow.is_connected,
            error=flow_state.error,
        )

    def handle_lan_event(self, event):
        """Consume one Pygame event while the LAN overlay owns the screen."""

        flow = self.lan_lobby_flow
        if flow is None or not flow.is_open:
            return False

        showing_match = bool(
            flow.match_active and flow.mode == "connected" and self.lan_match_visible
        )
        if showing_match:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self.lan_match_visible = False
                self.lan_match_layout = None
                return True
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if flow.command_pending or self.lan_match_layout is None:
                    return True
                target = hit_test_lan_match_display(
                    self.lan_match_layout,
                    event.pos,
                )
                if target is None:
                    return True
                if target.kind == "select_build_piece":
                    self.lan_selected_build_piece = target.build_piece
                    return True
                if target.kind == "command" and target.command is not None:
                    flow.send_game_command(target.command, dict(target.args))
                return True
            return True

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.lan_lobby_layout is None:
                return True
            target = hit_test_lan_lobby_display(self.lan_lobby_layout, event.pos)
            if target is None:
                return True
            if target.action == ACTION_START_MATCH and flow.match_active:
                self.lan_match_visible = True
                return True
            flow.handle_action(target.action)
            return True

        if event.type == pygame.TEXTINPUT:
            flow.append_text(event.text)
            return True

        if event.type != pygame.KEYDOWN:
            return True
        if event.key == pygame.K_ESCAPE:
            if flow.mode == "home":
                flow.handle_action(ACTION_CLOSE)
            elif flow.mode == "connected":
                flow.handle_action(ACTION_LEAVE_ROOM)
            else:
                flow.handle_action(ACTION_BACK)
            return True
        if event.key == pygame.K_BACKSPACE:
            flow.backspace()
            return True
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if flow.match_active and flow.mode == "connected":
                self.lan_match_visible = True
            else:
                flow.submit()
            return True
        return True

    def rebuild_pre_game_board(self, *, announcement=None):
        if not self.can_edit_pre_game_settings():
            self.notify_invalid("盤面設定は初期ダイスを振る前だけ変更できます。")
            return False

        player_count = len(self.players) or 2
        self.board = self.create_board_from_settings()
        self.get_board_rules().set_board(self.board)
        self.variant_state = self.create_initial_variant_state()
        self.configure_players(player_count, reset_logs=False)
        self.feedback.clear()
        self.clear_log()
        self.add_log(f"盤面設定: {self.get_board_configuration_summary()}")
        self.add_log("人数を決めて初期ダイスを開始してください。")
        if announcement:
            self.add_log(announcement)
        return True

    def apply_seed_text(self):
        if not self.can_edit_pre_game_settings():
            self.notify_invalid("seedは初期ダイスを振る前だけ変更できます。")
            return False
        if not self.board_seed_text.isdigit():
            self.notify_invalid("seed は半角数字で入力してください。")
            return False
        self.board_seed = int(self.board_seed_text)
        self.seed_input_active = False
        return self.rebuild_pre_game_board(
            announcement="seed を反映して盤面を更新しました。"
        )

    def randomize_board_seed(self):
        if not self.can_edit_pre_game_settings():
            self.notify_invalid("盤面は初期ダイスを振る前だけ再生成できます。")
            return False
        self.board_seed = self.generate_board_seed()
        self.board_seed_text = str(self.board_seed)
        self.seed_input_active = False
        return self.rebuild_pre_game_board(
            announcement="新しい seed で盤面を再生成しました。"
        )

    def restart_game(self, *, randomize_seed=False):
        player_count = len(self.players) or 2
        # A completed result reveals the previous lineup.  Rotate the private
        # seed before rebuilding so the next mixed match cannot be inferred
        # from that reveal, without consuming the gameplay RNG.
        self._mixed_ai_personality_seed = secrets.randbits(128)
        if randomize_seed:
            self.board_seed = self.generate_board_seed()
            if self.board_mode == "custom":
                self.board_mode = "constrained"
                self.custom_map_spec = None
        self.board_seed_text = str(self.board_seed)
        self.board = self.create_board_from_settings()
        self.get_board_rules().set_board(self.board)
        self.variant_state = self.create_initial_variant_state()
        self.configure_players(player_count, reset_logs=False)
        self.clear_log()
        self.add_log(f"再戦準備: {self.get_board_configuration_summary()}")
        self.add_log("人数・AI・勝利点・盤面を確認して、初期ダイスを振ってください。")

    def set_board_mode(self, mode):
        if not self.can_edit_pre_game_settings():
            self.notify_invalid("盤面modeは初期ダイスを振る前だけ変更できます。")
            return False
        if mode not in ("constrained", "fully_random"):
            return False
        if self.board_mode == mode:
            return False
        self.board_mode = mode
        self.custom_map_spec = None
        self.seed_input_active = False
        return self.rebuild_pre_game_board(
            announcement=f"盤面 mode を {mode} に切り替えました。"
        )

    def set_ai_player_count(self, ai_player_count):
        if not self.can_edit_pre_game_settings():
            self.notify_invalid("AI人数は初期ダイスを振る前だけ変更できます。")
            return False
        player_count = len(self.players) or 2
        self.ai_player_count = max(0, min(int(ai_player_count), player_count - 1))
        self.configure_players(player_count)
        return True

    def cycle_ai_player_count(self):
        player_count = len(self.players) or 2
        self.set_ai_player_count((self.ai_player_count + 1) % player_count)

    def adjust_victory_point_target(self, delta):
        if not self.can_edit_pre_game_settings():
            return False
        previous = self.victory_point_target
        self.victory_point_target = max(
            MIN_VICTORY_POINT_TARGET,
            min(MAX_VICTORY_POINT_TARGET, previous + int(delta)),
        )
        changed = self.victory_point_target != previous
        if changed:
            self.reset_replay_recording()
        return changed

    def reset_pending_dice_state(self):
        self.pending_dice_context = None
        self.pending_dice_roll = None
        self.pending_dice_player_name = ""
        self.dice_overlay.state = "idle"

    def clear_domestic_trade_state(self):
        self.domestic_trade_partner = None
        self.domestic_trade_give = {
            resource_type: 0 for resource_type in RESOURCE_TYPES
        }
        self.domestic_trade_receive = {
            resource_type: 0 for resource_type in RESOURCE_TYPES
        }
        self.domestic_trade_receive_operator = "and"
        self.domestic_trade_edit_side = "give"
        self.domestic_trade_editor = None
        self.domestic_trade_is_counter = False
        self.domestic_trade_is_broadcast = False
        self.domestic_trade_broadcast_responders = []
        self.domestic_trade_broadcast_index = -1
        self.domestic_trade_broadcast_give = {
            resource_type: 0 for resource_type in RESOURCE_TYPES
        }
        self.domestic_trade_broadcast_receive = {
            resource_type: 0 for resource_type in RESOURCE_TYPES
        }
        self.domestic_trade_broadcast_receive_operator = "and"
        self.domestic_trade_broadcast_viewer = None

    def clear_player_handoff_state(self):
        self.handoff_player = None
        self.handoff_return_phase = None
        self.handoff_context = ""

    def should_hide_for_handoff(self, previous_player, next_player):
        return bool(
            previous_player is not None
            and next_player is not None
            and previous_player is not next_player
            and not previous_player.is_ai
            and not next_player.is_ai
        )

    def begin_player_handoff(self, next_player, *, return_phase=None, context="手番"):
        self.handoff_player = next_player
        self.handoff_return_phase = return_phase
        self.handoff_context = context
        self.special_phase = "player_handoff"
        self.feedback.clear()

    def reveal_player_handoff(self):
        if self.special_phase != "player_handoff":
            return False
        handoff_player = self.handoff_player
        return_phase = self.handoff_return_phase
        self.clear_player_handoff_state()
        self.special_phase = return_phase
        if (
            self.domestic_trade_is_broadcast
            and return_phase == "domestic_trade_counter_response"
        ):
            self.domestic_trade_broadcast_viewer = handoff_player
        return True

    def is_domestic_trade_phase(self):
        return bool(
            self.special_phase and self.special_phase.startswith("domestic_trade_")
        )

    def reset_special_phase_state(self):
        self.special_phase = None
        self.discard_queue = []
        self.discard_player = None
        self.discard_remaining = 0
        self.robber_tile_candidates = []
        self.robber_target_players = []
        self.resource_selection_remaining = 0
        self.free_roads_remaining = 0
        self.bank_trade_give_resource = None
        self.clear_domestic_trade_state()
        self.clear_player_handoff_state()

    def reset_turn_state(self):
        self.dice_rolled = False
        self.last_dice_pair = None
        self.action_mode = None
        self.development_card_used_this_turn = False
        # A loan and its repayment are both meaningful economic actions.  One
        # credit action per turn prevents borrow -> repay -> borrow loops while
        # still letting the next player use the same public credit book.
        self.credit_action_taken_this_turn = False
        self.ai_domestic_trade_attempted = False
        self.ai_market_action_attempted = False
        self.ai_auction_action_attempted = False
        self.reset_special_phase_state()

    def is_forecast_variant(self):
        config = getattr(self, "variant_config", None)
        return bool(
            isinstance(config, VariantConfig)
            and config.has_component(FORECAST_EVENTS_KIND)
        )

    def is_frontier_variant(self):
        return self.variant_config.has_component(FRONTIER_KIND)

    def is_trade_market_variant(self):
        return self.variant_config.has_component(TRADE2_VARIANT_KIND)

    def is_trade_auction_variant(self):
        trade_config = self.get_variant_component_config(TRADE2_VARIANT_KIND)
        return bool(
            trade_config is not None
            and trade_config.options.get("catalog") == TRADE2_AUCTION_CATALOG
        )

    def is_credit_variant(self):
        return self.variant_config.has_component(CREDIT_VARIANT_KIND)

    def get_variant_component_config(self, kind):
        """Return one direct or nested component configuration."""

        return self.variant_config.component_config(kind)

    def get_variant_component_state(self, kind):
        """Return one direct or nested authority state."""

        variant_state = getattr(self, "variant_state", None)
        if variant_state is None:
            return None
        return variant_state.component_state(kind)

    def replace_variant_component_state(self, kind, component_state):
        """Publish one same-clock child mutation without touching siblings."""

        self.variant_state = self.variant_state.with_component_state(
            self.variant_config,
            kind,
            component_state,
        )

    def get_variant_completed_turns(self, kind):
        component = self.get_variant_component_state(kind)
        if component is None:
            return 0
        return component.public["completed_turns"]

    def is_frontier_tile_revealed(self, tile):
        if not self.is_frontier_variant():
            return True
        frontier_state = self.get_variant_component_state(FRONTIER_KIND)
        if frontier_state is None:  # pragma: no cover - fixed config invariant.
            return False
        return frontier_state.is_frontier_tile_revealed(tile.axial)

    def get_public_node_tiles(self, node):
        """Return only terrain an AI or viewer is allowed to evaluate."""

        return tuple(tile for tile in node.tiles if self.is_frontier_tile_revealed(tile))

    def is_frontier_harbor_revealed(self, harbor):
        if not self.is_frontier_variant():
            return True
        adjacent = self.board.get_edge_adjacent_tiles((harbor.node1, harbor.node2))
        return any(self.is_frontier_tile_revealed(tile) for tile in adjacent)

    def get_public_node_harbors(self, node):
        return tuple(
            harbor
            for harbor in node.harbors
            if self.is_frontier_harbor_revealed(harbor)
            and not self.is_forecast_harbor_blocked(harbor)
        )

    def get_frontier_edge_discovery_tiles(self, edge):
        if not self.is_frontier_variant():
            return ()
        return tuple(
            tile
            for tile in self.board.get_edge_adjacent_tiles(edge)
            if not self.is_frontier_tile_revealed(tile)
        )

    def get_frontier_edge_discovery_count(self, edge):
        return len(self.get_frontier_edge_discovery_tiles(edge))

    def frontier_edge_is_reachable(self, edge):
        if not self.is_frontier_variant():
            return True
        return any(
            self.is_frontier_tile_revealed(tile)
            for tile in self.board.get_edge_adjacent_tiles(edge)
        )

    def reveal_frontier_from_road(self, road):
        """Reveal hidden terrain touched by a newly built authority road."""

        hidden_tiles = self.get_frontier_edge_discovery_tiles((road.node1, road.node2))
        if not hidden_tiles:
            return ()
        frontier_state = self.get_variant_component_state(FRONTIER_KIND)
        if frontier_state is None:  # pragma: no cover - guarded by variant check.
            return ()
        next_frontier, revealed_axials = frontier_state.reveal_frontier_tiles(
            [tile.axial for tile in hidden_tiles]
        )
        if not revealed_axials:
            return ()
        self.replace_variant_component_state(FRONTIER_KIND, next_frontier)
        revealed_set = set(revealed_axials)
        revealed_tiles = tuple(
            tile for tile in hidden_tiles if tile.axial in revealed_set
        )
        descriptions = []
        for tile in revealed_tiles:
            resource = RESOURCE_LABELS.get(tile.resource_type, tile.resource_type.name)
            number = "数字なし" if tile.number is None else str(tile.number)
            descriptions.append(f"{resource} {number}")
        detail = " / ".join(descriptions)
        self.add_log(f"フロンティア発見 — {detail}")
        self.record_event(
            f"{road.owner.name}が未知タイルを発見",
            detail,
            level="success",
            actor=road.owner,
            include_in_turn=self.phase == "main",
        )
        return revealed_tiles

    def is_forecast_event_active(self, event_id):
        forecast_state = self.get_variant_component_state(FORECAST_EVENTS_KIND)
        return bool(
            forecast_state is not None
            and event_id in forecast_state.active_forecast_event_ids()
        )

    def get_next_forecast_event_id(self):
        forecast_state = self.get_variant_component_state(FORECAST_EVENTS_KIND)
        if forecast_state is None:
            return None
        return forecast_state.next_forecast_event_id()

    def get_next_forecast_parameters(self):
        forecast_state = self.get_variant_component_state(FORECAST_EVENTS_KIND)
        if forecast_state is None:
            return {}
        return dict(forecast_state.next_forecast_parameters())

    def get_next_forecast_turns_remaining(self):
        forecast_state = self.get_variant_component_state(FORECAST_EVENTS_KIND)
        if forecast_state is None:
            return None
        public = forecast_state.public
        return max(
            0,
            public["forecast"]["resolve_turn"] - public["completed_turns"],
        )

    def get_active_forecast_effect(self, event_id):
        forecast_state = self.get_variant_component_state(FORECAST_EVENTS_KIND)
        if forecast_state is None:
            return None
        return forecast_state.active_forecast_effect(event_id)

    def get_forecast_event_parameters(self, event_id, *, active=False):
        if active:
            effect = self.get_active_forecast_effect(event_id)
            return {} if effect is None else dict(effect.get("parameters", {}))
        if self.get_next_forecast_event_id() != event_id:
            return {}
        return self.get_next_forecast_parameters()

    @staticmethod
    def forecast_sector_label(sector):
        return {
            0: "東側",
            1: "南東側",
            2: "南西側",
            3: "西側",
            4: "北西側",
            5: "北東側",
        }.get(sector, "不明な方角")

    def describe_forecast_parameters(self, event_id, parameters):
        if event_id == HARBOR_BLOCKADE_EVENT_ID:
            harbor_id = self._forecast_harbor_id_from_parameters(parameters)
            if isinstance(harbor_id, str) and harbor_id.startswith("harbor-"):
                return f"対象: 交換所 #{int(harbor_id.split('-')[1]) + 1}"
            if "campaign_plan" in parameters:
                return "対象: 公開済み交換所なし（今回は発動なし）"
        if event_id == BANDIT_RAID_EVENT_ID:
            target_number = parameters.get("target_number")
            if isinstance(target_number, int):
                return f"対象数字: {target_number}"
        if event_id == EARTHQUAKE_EVENT_ID:
            return f"対象: {self.forecast_sector_label(parameters.get('sector'))}"
        return ""

    @staticmethod
    def _forecast_harbor_id_from_parameters(parameters):
        harbor_id = parameters.get("harbor_id")
        if isinstance(harbor_id, str):
            return harbor_id
        plan_document = parameters.get("campaign_plan")
        if plan_document is None:
            return None
        try:
            plan_document = {
                **dict(plan_document),
                "eligible_harbor_ids": list(
                    plan_document["eligible_harbor_ids"]
                ),
                "outcome": dict(plan_document["outcome"]),
            }
            return HarborBlockadePlan.from_public_document(
                plan_document
            ).target_harbor_id
        except (GrandCampaignError, KeyError, TypeError):
            return None

    def get_forecast_harbor(self, harbor_id):
        if not isinstance(harbor_id, str):
            return None
        # Keep this mapping identical to semantic Web/LAN target IDs.
        from game.network_protocol import build_board_reference_index

        return build_board_reference_index(self)["harbor"].get(harbor_id)

    def is_forecast_harbor_blocked(self, harbor):
        effect = self.get_active_forecast_effect(HARBOR_BLOCKADE_EVENT_ID)
        if effect is None:
            return False
        harbor_id = self._forecast_harbor_id_from_parameters(
            effect.get("parameters", {})
        )
        return self.get_forecast_harbor(harbor_id) is harbor

    def is_forecast_harbor_announced(self, harbor):
        parameters = self.get_forecast_event_parameters(HARBOR_BLOCKADE_EVENT_ID)
        harbor_id = self._forecast_harbor_id_from_parameters(parameters)
        return self.get_forecast_harbor(harbor_id) is harbor

    def get_forecast_edge_sector(self, edge):
        node1, node2 = edge
        center_x = sum(tile.x for tile in self.board.tiles) / len(self.board.tiles)
        center_y = sum(tile.y for tile in self.board.tiles) / len(self.board.tiles)
        midpoint_x = (node1.x + node2.x) / 2
        midpoint_y = (node1.y + node2.y) / 2
        angle = math.atan2(midpoint_y - center_y, midpoint_x - center_x)
        return int(((angle + math.pi / 6) % (2 * math.pi)) // (math.pi / 3))

    def is_forecast_edge_blocked(self, edge):
        effect = self.get_active_forecast_effect(EARTHQUAKE_EVENT_ID)
        if effect is None:
            return False
        sector = effect.get("parameters", {}).get("sector")
        return self.get_forecast_edge_sector(edge) == sector

    def is_forecast_edge_announced(self, edge):
        parameters = self.get_forecast_event_parameters(EARTHQUAKE_EVENT_ID)
        if not parameters:
            return False
        return self.get_forecast_edge_sector(edge) == parameters.get("sector")

    def is_road_usable(self, road):
        return not self.is_forecast_edge_blocked((road.node1, road.node2))

    def get_effective_road_cost(self, player):
        normal_cost = dict(BUILD_COSTS["road"])
        if not self.is_forecast_event_active(CONSTRUCTION_BOOM_EVENT_ID):
            return normal_cost, None

        wood = player.available_resource_count(ResourceType.WOOD)
        brick = player.available_resource_count(ResourceType.BRICK)
        if wood <= brick:
            waived = ResourceType.WOOD
            cost = {ResourceType.BRICK: 1}
        else:
            waived = ResourceType.BRICK
            cost = {ResourceType.WOOD: 1}
        return cost, waived

    def can_afford_road(self, player):
        if player is None:
            return False
        cost, _waived = self.get_effective_road_cost(player)
        return player.can_afford(cost)

    def consume_construction_boom(self):
        forecast_state = self.get_variant_component_state(FORECAST_EVENTS_KIND)
        if forecast_state is None:
            return False
        next_state, consumed = forecast_state.consume_forecast_effect(
            CONSTRUCTION_BOOM_EVENT_ID
        )
        if consumed:
            self.replace_variant_component_state(FORECAST_EVENTS_KIND, next_state)
        return consumed

    def apply_merchant_festival_bonus(self, players):
        if not self.is_forecast_event_active(MERCHANT_FESTIVAL_EVENT_ID):
            return {}
        players = tuple(players)
        available_cards = [
            resource_type
            for resource_type in RESOURCE_TYPES
            for _ in range(self.bank.available(resource_type))
        ]
        if len(available_cards) < len(players):
            self.add_log("商人祭ボーナスは銀行在庫不足のため配布されませんでした。")
            return {}

        grants = {}
        for player in players:
            resource_type = random.choice(available_cards)
            available_cards.remove(resource_type)
            if self.give_resource_from_bank(player, resource_type, 1) != 1:
                raise RuntimeError("商人祭ボーナスの銀行在庫が一致しません。")
            grants[player] = resource_type
            self.record_public_gain(player, {resource_type: 1}, "商人祭")
        detail = " / ".join(
            f"{player.name}: {RESOURCE_LABELS[resource_type]} +1"
            for player, resource_type in grants.items()
        )
        self.add_log(f"商人祭ボーナス — {detail}")
        self.record_event(
            "商人祭ボーナス",
            detail,
            level="success",
            include_in_turn=True,
        )
        return grants

    def resolve_bandit_raid(self):
        effect = self.get_active_forecast_effect(BANDIT_RAID_EVENT_ID)
        if effect is None:
            return None
        target_number = effect.get("parameters", {}).get("target_number")
        candidates = [
            tile
            for tile in self.board.tiles
            if tile.number == target_number and self.is_frontier_tile_revealed(tile)
        ]
        if not candidates:
            detail = f"対象数字{target_number}のタイルがなく、盗賊は移動しませんでした。"
            target = None
        else:
            alternatives = [
                tile for tile in candidates if tile is not self.board.robber_tile
            ]
            if alternatives:
                candidates = alternatives

            def production_score(tile):
                return (
                    sum(
                        get_token_pip_count(tile.number)
                        * node.building.resource_multiplier
                        for node in tile.corners
                        if node.building is not None
                    ),
                    -int(tile.axial[1]),
                    -int(tile.axial[0]),
                )

            target = max(candidates, key=production_score)
            self.board.move_robber_to(target)
            detail = (
                f"数字{target_number}のタイルへ移動。捨て札・略奪はありません。"
            )
            self.play_sound("robber")
        forecast_state = self.get_variant_component_state(FORECAST_EVENTS_KIND)
        if forecast_state is None:  # pragma: no cover - active effect implies it.
            raise RuntimeError("予告イベントstateがありません。")
        next_state, _consumed = forecast_state.consume_forecast_effect(
            BANDIT_RAID_EVENT_ID
        )
        self.replace_variant_component_state(FORECAST_EVENTS_KIND, next_state)
        self.add_log(f"山賊襲来 — {detail}")
        self.record_event(
            "山賊襲来を解決",
            detail,
            level="warning",
            include_in_turn=False,
        )
        return target

    def announce_initial_forecast_event(self):
        if not self.is_forecast_variant():
            return
        event_id = self.get_next_forecast_event_id()
        if event_id is None:
            return
        definition = event_definition(event_id)
        forecast_state = self.get_variant_component_state(FORECAST_EVENTS_KIND)
        if forecast_state is None:  # pragma: no cover - guarded above.
            return
        public = forecast_state.public
        remaining = (
            public["forecast"]["resolve_turn"] - public["completed_turns"]
        )
        parameter_detail = self.describe_forecast_parameters(
            event_id,
            public["forecast"].get("parameters", {}),
        )
        detail = f"あと{remaining}手番で発動: {definition.description}"
        if parameter_detail:
            detail = f"{parameter_detail} / {detail}"
        self.add_log(f"イベント予告 — {definition.title}: {detail}")
        self.record_event(
            f"イベント予告: {definition.title}",
            detail,
            level="warning",
            include_in_turn=False,
        )

    def advance_forecast_event_turn(self):
        if not self.is_forecast_variant():
            return None
        forecast_state = self.get_variant_component_state(FORECAST_EVENTS_KIND)
        forecast_config = self.get_variant_component_config(FORECAST_EVENTS_KIND)
        next_state, update = forecast_state.advance_forecast_turn(
            forecast_config,
            player_count=len(self.turn_order),
            revealed_harbor_ids=self.get_campaign_forecast_harbor_ids(),
        )
        self.replace_variant_component_state(FORECAST_EVENTS_KIND, next_state)
        self._handle_forecast_turn_update(update)
        return update

    def _handle_forecast_turn_update(self, update):
        """Emit effects and history for an already-published forecast update."""

        for event_id in update.expired_event_ids:
            definition = event_definition(event_id)
            self.add_log(f"イベント終了 — {definition.title}")
            self.record_event(
                f"イベント終了: {definition.title}",
                "一時効果が終了しました。",
                level="info",
                include_in_turn=False,
            )
            if event_id == EARTHQUAKE_EVENT_ID:
                self.update_longest_road()
        if update.activated_event_id is None:
            return
        activated = event_definition(update.activated_event_id)
        announced = event_definition(update.announced_event_id)
        forecast_config = self.get_variant_component_config(FORECAST_EVENTS_KIND)
        interval = forecast_config.options["event_interval_turns"]
        if update.skipped_event_id is not None:
            announced_parameter_detail = self.describe_forecast_parameters(
                update.announced_event_id,
                self.get_next_forecast_parameters(),
            )
            detail = (
                "予告時点で公開済みの交換所がなかったため、"
                "港湾封鎖は効果を発生させず終了しました。"
                f" / 次回予告: {announced.title}（あと{interval}手番）"
            )
            if announced_parameter_detail:
                detail += f" / {announced_parameter_detail}"
            self.add_log(f"イベント見送り — {activated.title}: {detail}")
            self.add_log(
                f"次回イベント予告 — {announced.title}: あと{interval}手番"
            )
            self.record_event(
                f"イベント見送り: {activated.title}",
                detail,
                level="info",
                include_in_turn=False,
            )
            return
        refresh_note = (
            "同じ効果が有効なため重複せず、発動時点を更新しました。 / "
            if update.refreshed_event_id is not None
            else ""
        )
        active_parameters = self.get_forecast_event_parameters(
            update.activated_event_id,
            active=True,
        )
        announced_parameters = self.get_next_forecast_parameters()
        active_parameter_detail = self.describe_forecast_parameters(
            update.activated_event_id,
            active_parameters,
        )
        announced_parameter_detail = self.describe_forecast_parameters(
            update.announced_event_id,
            announced_parameters,
        )
        detail = activated.description
        if active_parameter_detail:
            detail = f"{active_parameter_detail} / {detail}"
        detail = (
            f"{detail} / {refresh_note}次回予告: {announced.title}"
            f"（あと{interval}手番）"
        )
        if announced_parameter_detail:
            detail += f" / {announced_parameter_detail}"
        self.add_log(f"イベント発動 — {activated.title}: {detail}")
        self.add_log(
            f"次回イベント予告 — {announced.title}: あと{interval}手番"
        )
        self.record_event(
            f"イベント発動: {activated.title}",
            detail,
            level="warning",
            include_in_turn=False,
        )
        if update.activated_event_id == EARTHQUAKE_EVENT_ID:
            self.update_longest_road()
        elif update.activated_event_id == BANDIT_RAID_EVENT_ID:
            self.resolve_bandit_raid()

    def get_resource_credit_loans(self):
        credit_state = self.get_variant_component_state(CREDIT_VARIANT_KIND)
        if credit_state is None:
            return ()
        return credit_state.credit_book().open_loans

    def get_resource_credit_loan(self, player):
        credit_state = self.get_variant_component_state(CREDIT_VARIANT_KIND)
        if credit_state is None or player not in self.players:
            return None
        return credit_state.credit_book().get_loan_for_borrower(
            self.players.index(player)
        )

    def get_credit_vp_modifier(self, player):
        """Return the public negative score attached to an open loan."""

        credit_state = self.get_variant_component_state(CREDIT_VARIANT_KIND)
        if credit_state is None or player not in self.players:
            return 0
        return credit_state.credit_book().public_vp_modifier(
            self.players.index(player)
        )

    def is_resource_credit_action_available(self, player):
        return bool(
            self.is_credit_variant()
            and player in self.players
            and player is self.get_current_player()
            and self.phase == "main"
            and self.winner is None
            and self.dice_rolled
            and not self.has_active_dice_animation()
            and self.special_phase is None
            and self.action_mode is None
            and not self.credit_action_taken_this_turn
        )

    def can_borrow_resource_credit(self, player, resource_type=None):
        if not self.is_resource_credit_action_available(player):
            return False
        if self.get_resource_credit_loan(player) is not None:
            return False
        if resource_type is None:
            return any(self.bank.available(resource) > 0 for resource in RESOURCE_TYPES)
        return bool(
            resource_type in RESOURCE_TYPES and self.bank.available(resource_type) > 0
        )

    def can_repay_resource_credit(self, player, payment=None):
        if not self.is_resource_credit_action_available(player):
            return False
        loan = self.get_resource_credit_loan(player)
        if loan is None:
            return False
        available = player.resource_ledger.available_map()
        if payment is None:
            if loan.status == LOAN_ACTIVE:
                return bool(
                    available[loan.borrowed_resource] >= 1
                    and sum(available.values()) >= 2
                )
            return sum(available.values()) >= 1
        try:
            credit_state = self.get_variant_component_state(CREDIT_VARIANT_KIND)
            plan = credit_state.credit_book().plan_repay(
                borrower_index=self.players.index(player),
                loan_id=loan.loan_id,
                expected_revision=loan.revision,
                payment=payment,
                current_turn=credit_state.public["completed_turns"],
            )
        except ResourceCreditError:
            return False
        return all(
            available[resource] >= amount
            for resource, amount in plan.resource_mutations[0].bundle.items()
        )

    def choose_resource_credit_repayment(self, player):
        """Return one deterministic legal payment, primarily for headless AI."""

        if not self.can_repay_resource_credit(player):
            return None
        loan = self.get_resource_credit_loan(player)
        available = player.resource_ledger.available_map()
        if loan.status == LOAN_ACTIVE:
            payment = {loan.borrowed_resource: 1}
            candidates = [
                resource
                for resource in RESOURCE_TYPES
                if available[resource]
                - (1 if resource is loan.borrowed_resource else 0)
                > 0
            ]
            if not candidates:
                return None
            extra = max(
                candidates,
                key=lambda resource: (available[resource], -resource.value),
            )
            payment[extra] = payment.get(extra, 0) + 1
            return payment

        remaining = min(loan.remaining_cards, sum(available.values()))
        payment = {}
        for resource in sorted(
            RESOURCE_TYPES,
            key=lambda item: (-available[item], item.value),
        ):
            amount = min(available[resource], remaining)
            if amount:
                payment[resource] = amount
                remaining -= amount
            if remaining == 0:
                break
        return payment or None

    def _commit_resource_credit_plan(self, plan):
        credit_state = self.get_variant_component_state(CREDIT_VARIANT_KIND)
        credit_config = self.get_variant_component_config(CREDIT_VARIANT_KIND)
        next_state, result = credit_state.apply_credit_plan(
            credit_config,
            plan,
        )
        bank_snapshot = dict(self.bank.resources)
        player_snapshots = [
            (dict(player.resources), player.resource_ledger.to_document())
            for player in self.players
        ]
        try:
            for mutation in result.resource_mutations:
                player = self.players[mutation.player_index]
                if mutation.operation == BANK_TO_PLAYER:
                    if len(mutation.bundle) != 1:
                        raise RuntimeError("借入資源の移動内容が不正です。")
                    resource_type, amount = next(iter(mutation.bundle.items()))
                    if not self.bank.withdraw(resource_type, amount):
                        raise RuntimeError("銀行に借入対象の資源がありません。")
                    player.add_resource(resource_type, amount)
                elif mutation.operation == PLAYER_TO_BANK:
                    if not player.spend_resources(mutation.bundle):
                        raise RuntimeError("返済に使える資源が不足しています。")
                    self.bank.deposit_cost(mutation.bundle)
                    if any(
                        self.bank.available(resource) > self.bank.cards_per_resource
                        for resource in RESOURCE_TYPES
                    ):
                        raise RuntimeError("返済後の銀行資源が上限を超えました。")
                else:  # pragma: no cover - domain rejects unknown operations.
                    raise RuntimeError("未対応の信用資源操作です。")
        except Exception:
            self.bank.resources = bank_snapshot
            for player, (resources, ledger) in zip(self.players, player_snapshots):
                player.resources = resources
                player.restore_resource_ledger(ledger)
            raise
        self.replace_variant_component_state(CREDIT_VARIANT_KIND, next_state)
        return result

    def borrow_resource_credit(self, player, resource_type):
        if not self.can_borrow_resource_credit(player, resource_type):
            self.notify_invalid("現在は銀行から資源を借りられません。")
            return False
        try:
            credit_state = self.get_variant_component_state(CREDIT_VARIANT_KIND)
            plan = credit_state.credit_book().plan_borrow(
                borrower_index=self.players.index(player),
                borrowed_resource=resource_type,
                current_turn=credit_state.public["completed_turns"],
                player_count=len(self.players),
            )
            result = self._commit_resource_credit_plan(plan)
        except (ResourceCreditError, VariantStateError, RuntimeError, ValueError) as exc:
            self.notify_invalid(str(exc))
            return False
        loan = result.created_loan
        if loan is None:  # pragma: no cover - domain operation invariant.
            return False
        self.credit_action_taken_this_turn = True
        resource_label = RESOURCE_LABELS[resource_type]
        self.record_public_gain(player, {resource_type: 1}, "銀行借入")
        self.add_log(
            f"{player.name} が銀行から {resource_label} を1枚借りました。"
            f"期限 {loan.due_turn} / 公開VP -1"
        )
        self.record_event(
            f"{player.name}が資源を借入",
            f"{resource_label}1枚 / 期限 {loan.due_turn}完了手番 / 公開VP -1",
            level="warning",
            actor=player,
        )
        return True

    def repay_resource_credit(
        self,
        player,
        loan_id,
        expected_revision,
        payment,
    ):
        if not self.is_resource_credit_action_available(player):
            self.notify_invalid("現在はローンを返済できません。")
            return False
        try:
            credit_state = self.get_variant_component_state(CREDIT_VARIANT_KIND)
            loan = credit_state.credit_book().get_loan(loan_id)
            if loan is None or loan.borrower_index != self.players.index(player):
                raise ResourceCreditError("返済できるローンがありません。")
            plan = credit_state.credit_book().plan_repay(
                borrower_index=self.players.index(player),
                loan_id=loan_id,
                expected_revision=expected_revision,
                payment=payment,
                current_turn=credit_state.public["completed_turns"],
            )
            if not self.can_repay_resource_credit(player, payment):
                raise ResourceCreditError("返済条件または利用可能な資源が不足しています。")
            result = self._commit_resource_credit_plan(plan)
        except (ResourceCreditError, VariantStateError, RuntimeError, ValueError) as exc:
            self.notify_invalid(str(exc))
            return False
        self.credit_action_taken_this_turn = True
        paid_text = self.format_resource_bundle(payment)
        remaining = next(
            (
                updated.remaining_cards
                for updated in result.updated_loans
                if updated.loan_id == loan_id
            ),
            0,
        )
        detail = (
            f"{paid_text}を返済・残り{remaining}枚"
            if remaining
            else f"{paid_text}を返済・完済"
        )
        self.add_log(f"{player.name} がローンを返済: {detail}。")
        self.record_event(
            f"{player.name}がローンを返済",
            detail,
            level="success" if not remaining else "info",
            actor=player,
        )
        self.check_for_winner(player)
        return True

    def advance_resource_credit_turn(self):
        if not self.is_credit_variant():
            return ()
        credit_state = self.get_variant_component_state(CREDIT_VARIANT_KIND)
        credit_config = self.get_variant_component_config(CREDIT_VARIANT_KIND)
        next_state, result = credit_state.advance_credit_turn(
            credit_config
        )
        self.replace_variant_component_state(CREDIT_VARIANT_KIND, next_state)
        self._handle_credit_turn_update(result)
        return result.updated_loans

    def _handle_credit_turn_update(self, result):
        """Emit public delinquency effects for an already-published update."""

        for loan in result.updated_loans:
            player = self.players[loan.borrower_index]
            self.add_log(
                f"{player.name} のローンが延滞しました。"
                f"残債{loan.remaining_cards}枚 / 公開VP -2"
            )
            self.record_event(
                f"{player.name}のローンが延滞",
                f"残債{loan.remaining_cards}枚 / 公開VP -2",
                level="warning",
                actor=player,
                include_in_turn=False,
            )

    def get_trade_market_orders(self):
        """Return the public exact-fill orders for the standing-market mode."""

        if not self.is_trade_market_variant():
            return ()
        return self.get_variant_component_state(
            TRADE2_VARIANT_KIND
        ).trade_market().open_orders

    def is_trade_market_action_available(self, player):
        return bool(
            self.is_trade_market_variant()
            and self.phase == "main"
            and self.winner is None
            and self.special_phase is None
            and self.dice_rolled
            and player is self.get_current_player()
        )

    def can_create_trade_market_order(self, player):
        if not self.is_trade_market_action_available(player):
            return False
        market = self.get_variant_component_state(TRADE2_VARIANT_KIND).trade_market()
        seller_index = self.players.index(player)
        return bool(
            player.available_resource_total() > 0
            and len(market.open_orders) < MAX_OPEN_ORDERS
            and sum(
                order.seller_index == seller_index
                for order in market.open_orders
            )
            < MAX_OPEN_ORDERS_PER_SELLER
        )

    def can_fill_trade_market_order(self, player, order):
        if (
            not self.is_trade_market_action_available(player)
            or not isinstance(order, MarketOrder)
        ):
            return False
        buyer_index = self.players.index(player)
        current_turn = self.get_variant_completed_turns(TRADE2_VARIANT_KIND)
        return bool(
            order.seller_index != buyer_index
            and not order.is_expired(current_turn)
            and player.can_afford(order.wanted)
        )

    def create_trade_market_order(self, player, offer, wanted):
        if not self.can_create_trade_market_order(player):
            self.notify_invalid("現在は常設市場へ出品できません。")
            return False
        try:
            trade_state = self.get_variant_component_state(TRADE2_VARIANT_KIND)
            trade_config = self.get_variant_component_config(TRADE2_VARIANT_KIND)
            market = trade_state.trade_market()
            plan = market.plan_create(
                seller_index=self.players.index(player),
                offer=offer,
                wanted=wanted,
                current_turn=trade_state.public["completed_turns"],
                ttl=trade_config.options["order_ttl_turns"],
            )
            next_state, result = trade_state.apply_trade_market_plan(
                trade_config,
                plan,
            )
        except (TradeMarketError, VariantStateError, ValueError) as exc:
            self.notify_invalid(str(exc))
            return False

        order = result.created_order
        if order is None or not player.reserve_resources(
            order.reservation_id,
            order.offer,
        ):
            self.notify_invalid("出品する資源が不足しているか、すでに予約されています。")
            return False
        self.replace_variant_component_state(TRADE2_VARIANT_KIND, next_state)
        offer_text = self.format_resource_bundle(order.offer)
        wanted_text = self.format_resource_bundle(order.wanted)
        self.play_sound("card")
        self.add_log(
            f"{player.name} が常設市場へ出品: {offer_text} → {wanted_text}"
        )
        self.record_event(
            f"{player.name}が市場へ出品",
            f"{offer_text} → {wanted_text}",
            level="info",
            actor=player,
        )
        return True

    def cancel_trade_market_order(self, player, order_id, expected_revision):
        if not self.is_trade_market_action_available(player):
            self.notify_invalid("現在は常設市場の注文を取り消せません。")
            return False
        try:
            trade_state = self.get_variant_component_state(TRADE2_VARIANT_KIND)
            trade_config = self.get_variant_component_config(TRADE2_VARIANT_KIND)
            market = trade_state.trade_market()
            order = market.get_order(order_id)
            if order is None:
                raise TradeMarketError("指定した公開注文は存在しません。")
            plan = market.plan_cancel(
                requester_index=self.players.index(player),
                order_id=order_id,
                expected_revision=expected_revision,
            )
            next_state, _result = trade_state.apply_trade_market_plan(
                trade_config,
                plan,
            )
        except (TradeMarketError, VariantStateError, ValueError) as exc:
            self.notify_invalid(str(exc))
            return False

        reservation = player.resource_ledger.reservations_map().get(
            order.reservation_id
        )
        if reservation != dict(order.offer):
            self.notify_invalid("注文と予約資源が一致しません。")
            return False
        released = player.release_reserved_resources(order.reservation_id)
        if released != dict(order.offer):  # Defensive after the preflight check.
            raise RuntimeError("常設市場の予約解放に失敗しました。")
        self.replace_variant_component_state(TRADE2_VARIANT_KIND, next_state)
        self.add_log(f"{player.name} が常設市場の出品を取り消しました。")
        self.record_event(
            f"{player.name}が市場出品を取消",
            self.format_resource_bundle(order.offer),
            level="info",
            actor=player,
        )
        return True

    def cancel_all_trade_market_orders(self, player, *, reason):
        """Atomically cancel one player's public orders before a forced loss."""

        if not self.is_trade_market_variant() or player not in self.players:
            return 0
        cancelled_auction_positions = self.cancel_all_trade_auction_positions(
            player,
            reason=reason,
        )
        seller_index = self.players.index(player)
        orders = tuple(
            order
            for order in self.get_trade_market_orders()
            if order.seller_index == seller_index
        )
        if not orders:
            return cancelled_auction_positions
        reservations = player.resource_ledger.reservations_map()
        if any(
            reservations.get(order.reservation_id) != dict(order.offer)
            for order in orders
        ):
            raise RuntimeError("常設市場の注文と予約資源が一致しません。")

        next_state = self.get_variant_component_state(TRADE2_VARIANT_KIND)
        trade_config = self.get_variant_component_config(TRADE2_VARIANT_KIND)
        for order in orders:
            market = next_state.trade_market()
            plan = market.plan_cancel(
                requester_index=seller_index,
                order_id=order.order_id,
                expected_revision=order.revision,
            )
            next_state, _result = next_state.apply_trade_market_plan(
                trade_config,
                plan,
            )
        for order in orders:
            released = player.release_reserved_resources(order.reservation_id)
            if released != dict(order.offer):  # pragma: no cover - preflighted.
                raise RuntimeError("常設市場の予約解放に失敗しました。")
        self.replace_variant_component_state(TRADE2_VARIANT_KIND, next_state)
        detail = f"{reason}のため出品{len(orders)}件を自動取消"
        self.add_log(f"{player.name}: {detail}。")
        self.record_event(
            f"{player.name}の市場出品を自動取消",
            detail,
            level="warning",
            actor=player,
            include_in_turn=False,
        )
        return cancelled_auction_positions + len(orders)

    def fill_trade_market_order(self, player, order_id, expected_revision):
        if not self.is_trade_market_action_available(player):
            self.notify_invalid("現在は常設市場の注文を購入できません。")
            return False
        try:
            trade_state = self.get_variant_component_state(TRADE2_VARIANT_KIND)
            trade_config = self.get_variant_component_config(TRADE2_VARIANT_KIND)
            market = trade_state.trade_market()
            order = market.get_order(order_id)
            if order is None:
                raise TradeMarketError("指定した公開注文は存在しません。")
            if not self.can_fill_trade_market_order(player, order):
                raise TradeMarketError("この注文を購入する資源が不足しています。")
            plan = market.plan_fill(
                buyer_index=self.players.index(player),
                order_id=order_id,
                expected_revision=expected_revision,
                current_turn=trade_state.public["completed_turns"],
            )
            next_state, _result = trade_state.apply_trade_market_plan(
                trade_config,
                plan,
            )
        except (TradeMarketError, VariantStateError, ValueError) as exc:
            self.notify_invalid(str(exc))
            return False

        seller = self.players[order.seller_index]
        reservation = seller.resource_ledger.reservations_map().get(
            order.reservation_id
        )
        if reservation != dict(order.offer):
            self.notify_invalid("注文と出品者の予約資源が一致しません。")
            return False
        seller_snapshot = (
            dict(seller.resources),
            seller.resource_ledger.to_document(),
        )
        buyer_snapshot = (
            dict(player.resources),
            player.resource_ledger.to_document(),
        )
        try:
            consumed = seller.consume_reserved_resources(order.reservation_id)
            if consumed != dict(order.offer):
                raise RuntimeError("出品資源を消費できませんでした。")
            if not player.spend_resources(order.wanted):
                raise RuntimeError("購入資源を支払えませんでした。")
            for resource_type, amount in order.wanted.items():
                seller.add_resource(resource_type, amount)
            for resource_type, amount in order.offer.items():
                player.add_resource(resource_type, amount)
        except Exception:
            seller.resources = seller_snapshot[0]
            seller.restore_resource_ledger(seller_snapshot[1])
            player.resources = buyer_snapshot[0]
            player.restore_resource_ledger(buyer_snapshot[1])
            raise

        self.replace_variant_component_state(TRADE2_VARIANT_KIND, next_state)
        offer_text = self.format_resource_bundle(order.offer)
        wanted_text = self.format_resource_bundle(order.wanted)
        self.record_public_gain(player, order.offer, "常設市場")
        self.record_public_gain(seller, order.wanted, "常設市場")
        self.play_sound("card")
        self.add_log(
            f"常設市場で取引成立: {player.name} が {seller.name} へ"
            f" {wanted_text} を渡し、{offer_text} を受け取りました。"
        )
        self.record_event(
            f"{player.name}が市場注文を購入",
            f"{player.name}: -{wanted_text} / +{offer_text}",
            level="success",
            actor=player,
        )
        self.match_metrics.record_domestic_trade(
            self.get_match_metric_player_id(player),
            self.get_match_metric_player_id(seller),
        )
        return True

    def get_trade_auctions(self):
        """Return every open public auction in deterministic ID order."""

        if not self.is_trade_auction_variant():
            return ()
        return self.get_variant_component_state(
            TRADE2_VARIANT_KIND
        ).trade_auction().open_auctions

    def is_trade_auction_seller_action_available(self, player):
        return bool(
            self.is_trade_auction_variant()
            and self.phase == "main"
            and self.winner is None
            and self.special_phase is None
            and self.action_mode is None
            and self.dice_rolled
            and player is self.get_current_player()
        )

    def is_trade_auction_bid_action_available(self, player):
        return bool(
            self.is_trade_auction_variant()
            and player in self.players
            and self.phase == "main"
            and self.winner is None
            and self.special_phase is None
            and self.action_mode is None
            and self.dice_rolled
            and not self.has_active_dice_animation()
        )

    def can_create_trade_auction(self, player):
        if not self.is_trade_auction_seller_action_available(player):
            return False
        house = self.get_variant_component_state(TRADE2_VARIANT_KIND).trade_auction()
        seller_index = self.players.index(player)
        return bool(
            player.available_resource_total() > 0
            and len(house.open_auctions) < MAX_OPEN_AUCTIONS
            and sum(
                auction.seller_index == seller_index
                for auction in house.open_auctions
            )
            < MAX_OPEN_AUCTIONS_PER_SELLER
        )

    def can_bid_trade_auction(self, player, auction, offer=None):
        if (
            not self.is_trade_auction_bid_action_available(player)
            or not isinstance(auction, AuctionLot)
        ):
            return False
        bidder_index = self.players.index(player)
        current_turn = self.get_variant_completed_turns(TRADE2_VARIANT_KIND)
        if auction.seller_index == bidder_index or auction.is_expired(current_turn):
            return False
        previous = auction.get_bid(bidder_index)
        if offer is not None:
            available = player.resource_ledger.available_map()
            if previous is not None:
                for resource_type, amount in previous.offer.items():
                    available[resource_type] += amount
            return bool(
                offer
                and not set(offer).intersection(auction.offer)
                and sum(offer.values()) >= auction.minimum_bid_cards
                and all(
                    available.get(resource_type, 0) >= amount
                    for resource_type, amount in offer.items()
                )
            )
        eligible_total = 0
        previous_offer = previous.offer if previous is not None else {}
        for resource_type, amount in player.resource_ledger.available_map().items():
            if resource_type not in auction.offer:
                eligible_total += amount + previous_offer.get(resource_type, 0)
        return eligible_total >= auction.minimum_bid_cards

    def can_cancel_trade_auction_bid(self, player, auction):
        return bool(
            self.is_trade_auction_bid_action_available(player)
            and isinstance(auction, AuctionLot)
            and auction.get_bid(self.players.index(player)) is not None
            and not auction.is_expired(
                self.get_variant_completed_turns(TRADE2_VARIANT_KIND)
            )
        )

    def can_accept_trade_auction(self, player, auction):
        return bool(
            self.is_trade_auction_seller_action_available(player)
            and isinstance(auction, AuctionLot)
            and auction.seller_index == self.players.index(player)
            and auction.bids
            and not auction.is_expired(
                self.get_variant_completed_turns(TRADE2_VARIANT_KIND)
            )
        )

    def can_cancel_trade_auction(self, player, auction):
        return bool(
            self.is_trade_auction_seller_action_available(player)
            and isinstance(auction, AuctionLot)
            and auction.seller_index == self.players.index(player)
            and not auction.is_expired(
                self.get_variant_completed_turns(TRADE2_VARIANT_KIND)
            )
        )

    def _commit_trade_auction_plan(self, plan):
        trade_state = self.get_variant_component_state(TRADE2_VARIANT_KIND)
        trade_config = self.get_variant_component_config(TRADE2_VARIANT_KIND)
        next_state, result = trade_state.apply_trade_auction_plan(
            trade_config,
            plan,
        )
        snapshots = [
            (dict(player.resources), player.resource_ledger.to_document())
            for player in self.players
        ]
        try:
            for mutation in result.ledger_mutations:
                player = self.players[mutation.player_index]
                current = player.resource_ledger.reservations_map().get(
                    mutation.reservation_id
                )
                if mutation.operation == LEDGER_RESERVE:
                    applied = current is None and player.reserve_resources(
                        mutation.reservation_id,
                        mutation.bundle,
                    )
                elif mutation.operation == LEDGER_REPLACE:
                    applied = (
                        current == dict(mutation.previous_bundle)
                        and player.resource_ledger.replace(
                            mutation.reservation_id,
                            mutation.bundle,
                        )
                    )
                elif mutation.operation == LEDGER_RELEASE:
                    applied = (
                        current == dict(mutation.bundle)
                        and player.release_reserved_resources(
                            mutation.reservation_id
                        )
                        == dict(mutation.bundle)
                    )
                elif mutation.operation == LEDGER_CONSUME:
                    applied = (
                        current == dict(mutation.bundle)
                        and player.consume_reserved_resources(
                            mutation.reservation_id
                        )
                        == dict(mutation.bundle)
                    )
                else:  # pragma: no cover - domain rejects unknown operations.
                    applied = False
                if not applied:
                    raise RuntimeError("公開競売の予約資源を更新できませんでした。")
            for transfer in result.transfers:
                receiver = self.players[transfer.to_player_index]
                for resource_type, amount in transfer.bundle.items():
                    receiver.add_resource(resource_type, amount)
        except Exception:
            for player, (resources, ledger) in zip(self.players, snapshots):
                player.resources = resources
                player.restore_resource_ledger(ledger)
            raise
        self.replace_variant_component_state(TRADE2_VARIANT_KIND, next_state)
        return result

    def create_trade_auction(self, player, offer, minimum_bid_cards):
        if not self.can_create_trade_auction(player):
            self.notify_invalid("現在は公開競売を開けません。")
            return False
        try:
            trade_state = self.get_variant_component_state(TRADE2_VARIANT_KIND)
            trade_config = self.get_variant_component_config(TRADE2_VARIANT_KIND)
            house = trade_state.trade_auction()
            plan = house.plan_create(
                seller_index=self.players.index(player),
                offer=offer,
                minimum_bid_cards=minimum_bid_cards,
                current_turn=trade_state.public["completed_turns"],
                ttl=trade_config.options["auction_ttl_turns"],
            )
            result = self._commit_trade_auction_plan(plan)
        except (TradeAuctionError, VariantStateError, ValueError, RuntimeError) as exc:
            self.notify_invalid(str(exc))
            return False
        auction = result.created_auction
        if auction is None:  # pragma: no cover - domain operation invariant.
            return False
        lot_text = self.format_resource_bundle(auction.offer)
        self.play_sound("card")
        self.add_log(
            f"{player.name} が公開競売を開始: {lot_text} / "
            f"最低{auction.minimum_bid_cards}枚"
        )
        self.record_event(
            f"{player.name}が公開競売を開始",
            f"出品 {lot_text} / 最低入札 {auction.minimum_bid_cards}枚",
            level="info",
            actor=player,
        )
        self._invite_ai_trade_auction_bids(auction.auction_id)
        return True

    def _invite_ai_trade_auction_bids(self, auction_id):
        """Let AI seats react once using only the public lot and their hand."""

        for candidate in self.players:
            if not candidate.is_ai:
                continue
            house = self.get_variant_component_state(
                TRADE2_VARIANT_KIND
            ).trade_auction()
            auction = house.get_auction(auction_id)
            if auction is None or auction.seller_index == self.players.index(candidate):
                continue
            offer = self.ai.choose_trade_auction_bid(self, candidate, auction)
            if offer is None:
                continue
            self.set_ai_status(
                candidate,
                "公開競売へ入札",
                "出品資源が建設目標の不足を補うため、余剰資源を提示します",
            )
            self.bid_trade_auction(
                candidate,
                auction.auction_id,
                auction.revision,
                offer,
            )

    def bid_trade_auction(self, player, auction_id, expected_revision, offer):
        try:
            trade_state = self.get_variant_component_state(TRADE2_VARIANT_KIND)
            house = trade_state.trade_auction()
            auction = house.get_auction(auction_id)
            if auction is None or not self.can_bid_trade_auction(
                player,
                auction,
                offer,
            ):
                raise TradeAuctionError("この競売へ入札できません。")
            plan = house.plan_bid(
                bidder_index=self.players.index(player),
                auction_id=auction_id,
                expected_revision=expected_revision,
                offer=offer,
                current_turn=trade_state.public["completed_turns"],
            )
            result = self._commit_trade_auction_plan(plan)
        except (TradeAuctionError, VariantStateError, ValueError, RuntimeError) as exc:
            self.notify_invalid(str(exc))
            return False
        bid = result.updated_auction.get_bid(self.players.index(player))
        bid_text = self.format_resource_bundle(bid.offer)
        self.play_sound("card")
        self.add_log(f"{player.name} が公開競売へ入札: {bid_text}")
        self.record_event(
            f"{player.name}が競売へ入札",
            bid_text,
            level="info",
            actor=player,
        )
        return True

    def cancel_trade_auction_bid(
        self,
        player,
        auction_id,
        expected_revision,
    ):
        try:
            trade_state = self.get_variant_component_state(TRADE2_VARIANT_KIND)
            house = trade_state.trade_auction()
            auction = house.get_auction(auction_id)
            if auction is None or not self.can_cancel_trade_auction_bid(
                player,
                auction,
            ):
                raise TradeAuctionError("取り消せる入札がありません。")
            plan = house.plan_cancel_bid(
                bidder_index=self.players.index(player),
                auction_id=auction_id,
                expected_revision=expected_revision,
                current_turn=trade_state.public["completed_turns"],
            )
            self._commit_trade_auction_plan(plan)
        except (TradeAuctionError, VariantStateError, ValueError, RuntimeError) as exc:
            self.notify_invalid(str(exc))
            return False
        self.add_log(f"{player.name} が公開競売の入札を取り消しました。")
        return True

    def accept_trade_auction(
        self,
        player,
        auction_id,
        expected_revision,
        bidder_index,
    ):
        try:
            trade_state = self.get_variant_component_state(TRADE2_VARIANT_KIND)
            house = trade_state.trade_auction()
            auction = house.get_auction(auction_id)
            if auction is None or not self.can_accept_trade_auction(player, auction):
                raise TradeAuctionError("現在はこの競売の落札者を決定できません。")
            plan = house.plan_accept(
                seller_index=self.players.index(player),
                auction_id=auction_id,
                expected_revision=expected_revision,
                bidder_index=bidder_index,
                current_turn=trade_state.public["completed_turns"],
            )
            result = self._commit_trade_auction_plan(plan)
        except (TradeAuctionError, VariantStateError, ValueError, RuntimeError) as exc:
            self.notify_invalid(str(exc))
            return False
        auction = result.removed_auctions[0]
        winner = self.players[result.accepted_bid.bidder_index]
        lot_text = self.format_resource_bundle(auction.offer)
        bid_text = self.format_resource_bundle(result.accepted_bid.offer)
        self.record_public_gain(winner, auction.offer, "公開競売")
        self.record_public_gain(player, result.accepted_bid.offer, "公開競売")
        self.play_sound("victory")
        self.add_log(
            f"公開競売成立: {winner.name} が {bid_text} を支払い、"
            f"{lot_text} を落札しました。"
        )
        self.record_event(
            f"{winner.name}が公開競売で落札",
            f"-{bid_text} / +{lot_text}",
            level="success",
            actor=winner,
        )
        self.match_metrics.record_domestic_trade(
            self.get_match_metric_player_id(player),
            self.get_match_metric_player_id(winner),
        )
        return True

    def cancel_trade_auction(self, player, auction_id, expected_revision):
        try:
            trade_state = self.get_variant_component_state(TRADE2_VARIANT_KIND)
            house = trade_state.trade_auction()
            auction = house.get_auction(auction_id)
            if auction is None or not self.can_cancel_trade_auction(player, auction):
                raise TradeAuctionError("現在はこの競売を取り消せません。")
            plan = house.plan_cancel(
                seller_index=self.players.index(player),
                auction_id=auction_id,
                expected_revision=expected_revision,
                current_turn=trade_state.public["completed_turns"],
            )
            self._commit_trade_auction_plan(plan)
        except (TradeAuctionError, VariantStateError, ValueError, RuntimeError) as exc:
            self.notify_invalid(str(exc))
            return False
        self.add_log(f"{player.name} が公開競売を取り消しました。")
        return True

    def cancel_all_trade_auction_positions(self, player, *, reason):
        """Cancel every seller lot and bid owned by a forced-loss player."""

        if not self.is_trade_auction_variant() or player not in self.players:
            return 0
        player_index = self.players.index(player)
        cancelled = 0
        while True:
            trade_state = self.get_variant_component_state(TRADE2_VARIANT_KIND)
            house = trade_state.trade_auction()
            auction = next(
                (
                    candidate
                    for candidate in house.open_auctions
                    if candidate.seller_index == player_index
                    or candidate.get_bid(player_index) is not None
                ),
                None,
            )
            if auction is None:
                break
            if auction.seller_index == player_index:
                plan = house.plan_cancel(
                    seller_index=player_index,
                    auction_id=auction.auction_id,
                    expected_revision=auction.revision,
                    current_turn=trade_state.public["completed_turns"],
                )
            else:
                plan = house.plan_cancel_bid(
                    bidder_index=player_index,
                    auction_id=auction.auction_id,
                    expected_revision=auction.revision,
                    current_turn=trade_state.public["completed_turns"],
                )
            self._commit_trade_auction_plan(plan)
            cancelled += 1
        if cancelled:
            detail = f"{reason}のため競売・入札{cancelled}件を自動取消"
            self.add_log(f"{player.name}: {detail}。")
            self.record_event(
                f"{player.name}の競売予約を自動取消",
                detail,
                level="warning",
                actor=player,
                include_in_turn=False,
            )
        return cancelled

    def advance_trade_market_turn(self):
        if not self.is_trade_market_variant():
            return ()
        trade_state = self.get_variant_component_state(TRADE2_VARIANT_KIND)
        trade_config = self.get_variant_component_config(TRADE2_VARIANT_KIND)
        next_state, expired, auction_result = trade_state.advance_trade2_turn(
            trade_config
        )
        self._apply_trade_expiry_resources(expired, auction_result)
        self.replace_variant_component_state(TRADE2_VARIANT_KIND, next_state)
        self._handle_trade_expiry_update(expired, auction_result)
        return expired

    def _apply_trade_expiry_resources(self, expired, auction_result):
        """Release all expired escrow as one rollback-safe external commit."""

        reservations_by_player = {
            index: player.resource_ledger.reservations_map()
            for index, player in enumerate(self.players)
        }
        if any(
            reservations_by_player[order.seller_index].get(order.reservation_id)
            != dict(order.offer)
            for order in expired
        ):
            raise RuntimeError("期限切れ注文と予約資源が一致しません。")
        auction_mutations = (
            auction_result.ledger_mutations if auction_result is not None else ()
        )
        if any(
            mutation.operation != LEDGER_RELEASE
            or reservations_by_player[mutation.player_index].get(
                mutation.reservation_id
            )
            != dict(mutation.bundle)
            for mutation in auction_mutations
        ):
            raise RuntimeError("期限切れ競売と予約資源が一致しません。")
        snapshots = [
            (dict(player.resources), player.resource_ledger.to_document())
            for player in self.players
        ]
        try:
            for order in expired:
                player = self.players[order.seller_index]
                released = player.release_reserved_resources(order.reservation_id)
                if released != dict(order.offer):
                    raise RuntimeError("期限切れ注文の予約解放に失敗しました。")
            for mutation in auction_mutations:
                player = self.players[mutation.player_index]
                released = player.release_reserved_resources(
                    mutation.reservation_id
                )
                if released != dict(mutation.bundle):
                    raise RuntimeError("期限切れ競売の予約解放に失敗しました。")
        except Exception:
            for player, (resources, ledger) in zip(self.players, snapshots):
                player.resources = resources
                player.restore_resource_ledger(ledger)
            raise

    def _handle_trade_expiry_update(self, expired, auction_result):
        """Emit history for an already-published trade expiry update."""

        if expired:
            self.add_log(f"常設市場: 出品{len(expired)}件が期限切れになりました。")
            self.record_event(
                "市場注文の期限切れ",
                f"{len(expired)}件の出品を終了",
                level="info",
                include_in_turn=False,
            )
        expired_auctions = (
            auction_result.removed_auctions if auction_result is not None else ()
        )
        if expired_auctions:
            self.add_log(
                f"公開競売: {len(expired_auctions)}件が期限切れになりました。"
            )
            self.record_event(
                "公開競売の期限切れ",
                f"{len(expired_auctions)}件を終了し、全入札資源を返却",
                level="info",
                include_in_turn=False,
            )

    def advance_variant_turn_boundary(self):
        """Advance optional rules once, with one clock publication boundary."""

        if self.variant_config.kind != COMPOSITE_VARIANT_KIND:
            self.advance_forecast_event_turn()
            self.advance_trade_market_turn()
            self.advance_resource_credit_turn()
            return

        next_state, update = self.variant_state.advance_composite_turn(
            self.variant_config,
            player_count=len(self.turn_order),
            revealed_harbor_ids=self.get_campaign_forecast_harbor_ids(),
        )
        # Escrow is external to VariantState.  Validate and mutate it before
        # publishing the parent and its three N+1 child clocks.  The helper
        # restores every player ledger on any release failure.
        self._apply_trade_expiry_resources(
            update.expired_market_orders,
            update.auction,
        )
        self.variant_state = next_state
        self._handle_forecast_turn_update(update.forecast)
        self._handle_trade_expiry_update(
            update.expired_market_orders,
            update.auction,
        )
        self._handle_credit_turn_update(update.credit)

    def reset_initial_setup_state(self):
        self.initial_dice_phase = True
        self.initial_dice_results = {}
        self.initial_dice_histories = {player.name: [] for player in self.players}
        self.initial_dice_contenders = self.players.copy()
        self.initial_dice_pending_groups = []
        self.initial_placement_order = []
        self.initial_placement_counts = {player.name: 0 for player in self.players}
        self.initial_round = 1
        self.initial_player_index = 0
        self.waiting_for_road = False
        self.last_settlement_node = None

    def configure_players(
        self,
        player_count,
        reset_logs=True,
        *,
        schedule_ai=True,
        reset_replay=True,
    ):
        self.ai_player_count = min(self.ai_player_count, max(0, player_count - 1))
        human_player_count = player_count - self.ai_player_count
        self.players = []
        cpu_index = 1
        player_markers = ("●", "◆", "▲", "■")
        for index, (default_name, color) in enumerate(
            self.player_palette[:player_count]
        ):
            is_ai = index >= human_player_count
            name = f"CPU{cpu_index}" if is_ai else default_name
            self.players.append(
                Player(
                    name,
                    color,
                    is_ai=is_ai,
                    piece_pattern=index,
                    marker=player_markers[index],
                )
            )
            if is_ai:
                cpu_index += 1
        self.assign_ai_personalities()
        self.reset_match_metrics()
        self.public_gain_history = {player.name: [] for player in self.players}
        self.last_resource_distribution = {}
        self.ai_status = {
            "player_name": "",
            "title": "AI待機中",
            "detail": "AIの手番になると判断内容を表示します。",
        }
        self.bank = ResourceBank()
        self.turn_order = self.players.copy()
        self.reset_initial_setup_state()
        self.current_player_index = 0
        self.reset_turn_state()
        self.winner = None
        self.longest_road_owner = None
        self.longest_road_length = 0
        self.largest_army_owner = None
        self.largest_army_size = 0
        self.reset_pending_dice_state()
        self.phase = "initial"
        self.development_deck = create_development_deck(
            disabled_cards=self.house_rules.disabled_development_cards
        )
        if schedule_ai:
            self.schedule_ai_action()
        self.buttons = [] if self.headless else self.build_buttons()
        self.show_help_panel = False
        self.seed_input_active = False
        self.feedback.clear()
        self.latest_event = {
            "title": "ゲーム準備中",
            "detail": "人数・AI・盤面を確認して初期ダイスを振ってください。",
            "level": "info",
            "color": COLORS["PANEL_BORDER"],
        }
        self.turn_summary_entries = []
        if reset_logs:
            self.clear_log()
            self.add_log(
                f"{player_count} 人プレイ（AI {self.ai_player_count} 人）に設定しました。"
            )
            if player_count == 2:
                self.add_log(
                    "2人プレイは公式基本ゲーム外の簡易バリアントです。公式準拠は3〜4人です。"
                )
            self.add_log(
                "人間プレイヤーはスペースキーで初期ダイスを振ります。AIは自動で進行します。"
            )
        if reset_replay and self.replay_recorder is not None:
            self.reset_replay_recording()

    def get_current_player(self):
        if not self.turn_order:
            return None
        return self.turn_order[self.current_player_index]

    def pay_resource_cost(self, player, cost):
        if not player.spend_resources(cost):
            return False
        self.bank.deposit_cost(cost)
        return True

    def give_resource_from_bank(
        self, player, resource_type, amount=1, *, allow_partial=False
    ):
        if allow_partial:
            amount = self.bank.withdraw_up_to(resource_type, amount)
        elif not self.bank.withdraw(resource_type, amount):
            return 0
        player.add_resource(resource_type, amount)
        return amount

    def get_player_victory_points(self, player):
        points = 0
        for node in self.board.nodes:
            if node.building is not None and node.building.owner == player:
                points += node.building.victory_points
        points += player.victory_point_cards
        if self.longest_road_owner == player:
            points += 2
        if self.largest_army_owner == player:
            points += 2
        return max(0, points + self.get_credit_vp_modifier(player))

    def get_player_public_victory_points(self, player):
        return self.get_player_victory_points(player) - player.victory_point_cards

    def get_points_by_player(self):
        if self.phase == "finished":
            return {
                player.name: self.get_player_victory_points(player)
                for player in self.players
            }
        return {
            player.name: self.get_player_public_victory_points(player)
            for player in self.players
        }

    def get_discard_key_map(self):
        return {
            pygame.K_1: ResourceType.WOOD,
            pygame.K_KP1: ResourceType.WOOD,
            pygame.K_2: ResourceType.SHEEP,
            pygame.K_KP2: ResourceType.SHEEP,
            pygame.K_3: ResourceType.WHEAT,
            pygame.K_KP3: ResourceType.WHEAT,
            pygame.K_4: ResourceType.BRICK,
            pygame.K_KP4: ResourceType.BRICK,
            pygame.K_5: ResourceType.ORE,
            pygame.K_KP5: ResourceType.ORE,
        }

    def get_resource_button_action(self, resource_type):
        return f"select_resource_{resource_type.name}"

    def get_selectable_resources(self):
        resources = [
            ResourceType.WOOD,
            ResourceType.SHEEP,
            ResourceType.WHEAT,
            ResourceType.BRICK,
            ResourceType.ORE,
        ]
        if self.special_phase == "discard" and self.discard_player is not None:
            return [
                resource_type
                for resource_type in resources
                if self.discard_player.resources.get(resource_type, 0) > 0
            ]
        if self.special_phase == "year_of_plenty":
            return [
                resource_type
                for resource_type in resources
                if self.bank.available(resource_type) > 0
            ]
        if self.special_phase == "monopoly":
            return resources
        return []

    def get_development_card_counts(self):
        return {
            player.name: {
                "knight": player.development_cards[DevelopmentCardType.KNIGHT],
                "road_building": player.development_cards[
                    DevelopmentCardType.ROAD_BUILDING
                ],
                "year_of_plenty": player.development_cards[
                    DevelopmentCardType.YEAR_OF_PLENTY
                ],
                "monopoly": player.development_cards[DevelopmentCardType.MONOPOLY],
                "victory_point": player.victory_point_cards,
                "new_cards": sum(player.new_development_cards.values()),
                "played_knights": player.played_knights,
            }
            for player in self.players
        }

    def get_current_player_development_summary(self):
        player = self.get_current_player()
        if player is None:
            return ""
        if player.is_ai:
            return "非公開"
        parts = [
            f"K:{player.development_cards[DevelopmentCardType.KNIGHT]}",
            f"B:{player.development_cards[DevelopmentCardType.ROAD_BUILDING]}",
            f"Y:{player.development_cards[DevelopmentCardType.YEAR_OF_PLENTY]}",
            f"M:{player.development_cards[DevelopmentCardType.MONOPOLY]}",
            f"VP:{player.victory_point_cards}",
        ]
        if sum(player.new_development_cards.values()) > 0:
            parts.append(f"新規:{sum(player.new_development_cards.values())}")
        return " ".join(parts)

    def get_point_breakdown(self, player):
        settlement_points = 0
        city_points = 0
        for node in self.board.nodes:
            if node.building is None or node.building.owner != player:
                continue
            if node.building.building_type == BuildingType.CITY:
                city_points += 2
            else:
                settlement_points += 1
        return {
            "settlement": settlement_points,
            "city": city_points,
            "longest_road": 2 if self.longest_road_owner == player else 0,
            "largest_army": 2 if self.largest_army_owner == player else 0,
            "vp_card": player.victory_point_cards,
            "debt_penalty": self.get_credit_vp_modifier(player),
        }

    def get_all_point_breakdowns(self):
        return {
            player.name: self.get_point_breakdown(player) for player in self.players
        }

    def get_missing_resources(self, player, cost):
        missing = []
        for resource_type, required in cost.items():
            shortage = required - player.available_resource_count(resource_type)
            if shortage > 0:
                missing.append(f"{RESOURCE_LABELS[resource_type]}{shortage}")
        return missing

    def get_build_preview(
        self, label, player, cost, supply_available=True, unavailable_reason="在庫なし"
    ):
        if not supply_available:
            return {"label": label, "available": False, "detail": unavailable_reason}

        missing = self.get_missing_resources(player, cost)
        if missing:
            return {
                "label": label,
                "available": False,
                "detail": f"不足: {' '.join(missing)}",
            }

        return {"label": label, "available": True, "detail": "建設可"}

    def get_build_affordability(self, player):
        if player is None:
            return []
        road_cost, road_discount = self.get_effective_road_cost(player)
        road_preview = self.get_build_preview(
            "街道",
            player,
            road_cost,
            player.roads_remaining > 0,
        )
        if road_discount is not None and road_preview["available"]:
            road_preview["detail"] = (
                f"建設可（建設ブーム: {RESOURCE_LABELS[road_discount]}免除）"
            )
        settlement_preview = self.get_build_preview(
            "開拓地",
            player,
            BUILD_COSTS["settlement"],
            player.settlements_remaining > 0,
        )
        city_preview = self.get_build_preview(
            "都市",
            player,
            BUILD_COSTS["city"],
            player.cities_remaining > 0,
        )
        if road_preview["available"] and not self.get_buildable_road_edges(
            player,
            require_affordability=False,
        ):
            road_preview = {"label": "街道", "available": False, "detail": "接続先なし"}
        if settlement_preview["available"] and not any(
            self.can_place_main_settlement(player, node)[0] for node in self.board.nodes
        ):
            settlement_preview = {
                "label": "開拓地",
                "available": False,
                "detail": "建設候補なし",
            }
        if city_preview["available"] and not any(
            self.can_upgrade_to_city(player, node)[0] for node in self.board.nodes
        ):
            city_preview = {
                "label": "都市",
                "available": False,
                "detail": "対象開拓地なし",
            }
        return [
            road_preview,
            settlement_preview,
            city_preview,
            self.get_build_preview(
                "発展",
                player,
                BUILD_COSTS["development"],
                bool(self.development_deck),
                "山札なし",
            ),
        ]

    def get_trade_rates(self, player):
        if player is None:
            return {}
        rates = {resource_type: 4 for resource_type in RESOURCE_TYPES}
        seen_harbors = set()
        for node in self.board.nodes:
            if node.building is None or node.building.owner is not player:
                continue
            for harbor in self.get_public_node_harbors(node):
                if harbor in seen_harbors:
                    continue
                seen_harbors.add(harbor)
                if harbor.resource_type is None:
                    for resource_type in rates:
                        rates[resource_type] = min(rates[resource_type], harbor.trade_rate)
                else:
                    rates[harbor.resource_type] = min(
                        rates[harbor.resource_type], harbor.trade_rate
                    )
        if self.house_rules.bank_trade_3_to_1:
            return {
                resource_type: min(rate, 3) for resource_type, rate in rates.items()
            }
        return rates

    def get_buildable_road_edges(self, player, require_affordability=True):
        candidates = self.get_board_rules().get_buildable_road_edges(
            player,
            require_affordability=False,
        )
        if require_affordability and not self.can_afford_road(player):
            return []
        return [
            edge
            for edge in candidates
            if self.frontier_edge_is_reachable(edge)
            and not self.is_forecast_edge_blocked(edge)
        ]

    def get_buildable_settlement_nodes(self, player):
        return [
            node
            for node in self.get_board_rules().get_buildable_settlement_nodes(player)
            if self.get_public_node_tiles(node)
        ]

    def get_buildable_city_nodes(self, player):
        return self.get_board_rules().get_buildable_city_nodes(player)

    def get_initial_settlement_candidates(self):
        return [
            node
            for node in self.get_board_rules().get_initial_settlement_candidates()
            if self.get_public_node_tiles(node)
        ]

    def get_initial_road_candidates(self, player):
        candidates = self.get_board_rules().get_initial_road_candidates(
            player, self.last_settlement_node
        )
        return [edge for edge in candidates if self.frontier_edge_is_reachable(edge)]

    def get_steal_target_nodes(self):
        return self.get_board_rules().get_steal_target_nodes(self.robber_target_players)

    def get_board_highlight_data(self):
        initial_player = None
        if (
            self.phase == "initial"
            and not self.initial_dice_phase
            and self.initial_placement_order
        ):
            initial_player = self.initial_placement_order[self.initial_player_index]

        highlights = self.get_board_rules().get_board_highlights(
            BoardHighlightState(
                phase=self.phase,
                initial_dice_phase=self.initial_dice_phase,
                waiting_for_road=self.waiting_for_road,
                special_phase=self.special_phase,
                action_mode=self.action_mode,
                winner_present=self.winner is not None,
                has_active_dice_animation=self.has_active_dice_animation(),
                current_player=self.get_current_player(),
                initial_placement_player=initial_player,
                robber_tile_candidates=tuple(self.robber_tile_candidates),
                robber_target_players=tuple(self.robber_target_players),
                last_settlement_node=self.last_settlement_node,
            )
        )
        highlights["settlement_nodes"] = [
            node
            for node in highlights["settlement_nodes"]
            if self.get_public_node_tiles(node)
        ]
        highlights["edge_highlights"] = [
            edge
            for edge in highlights["edge_highlights"]
            if self.frontier_edge_is_reachable(edge)
        ]
        highlights["tile_highlights"] = [
            tile
            for tile in highlights["tile_highlights"]
            if self.is_frontier_tile_revealed(tile)
        ]
        return highlights

    def get_phase_tracker_data(self):
        if self.phase == "initial" and self.initial_dice_phase:
            return (
                "セットアップ進行",
                "人数と順番を決定",
                [
                    PhaseStep("人数", "complete"),
                    PhaseStep("初期ダイス", "active"),
                    PhaseStep("初期配置", "pending"),
                ],
            )

        if self.phase == "initial":
            if self.initial_round == 1:
                return (
                    "初期配置",
                    "1周目の配置中",
                    [
                        PhaseStep("配置1", "active"),
                        PhaseStep("配置2", "pending"),
                        PhaseStep("開始", "pending"),
                    ],
                )
            return (
                "初期配置",
                "2周目の配置中",
                [
                    PhaseStep("配置1", "complete"),
                    PhaseStep("配置2", "active"),
                    PhaseStep("開始", "pending"),
                ],
            )

        if self.phase == "finished":
            return (
                "ゲーム終了",
                f"勝者: {self.winner.name}" if self.winner is not None else "",
                [
                    PhaseStep("ダイス", "complete"),
                    PhaseStep("行動", "complete"),
                    PhaseStep("勝利", "active"),
                ],
            )

        if self.special_phase == "player_handoff":
            return (
                "プレイヤー交代",
                f"{self.handoff_player.name} に画面を渡す",
                [
                    PhaseStep("非表示", "complete"),
                    PhaseStep("交代", "active"),
                    PhaseStep("再開", "pending"),
                ],
            )

        if self.is_domestic_trade_phase():
            return (
                "国内交易",
                "交渉条件を確認",
                [
                    PhaseStep("ダイス", "complete"),
                    PhaseStep("交渉", "active"),
                    PhaseStep("終了", "pending"),
                ],
            )

        if self.special_phase is not None:
            return (
                "ターン進行",
                "特殊処理を完了",
                [
                    PhaseStep("ダイス", "complete"),
                    PhaseStep("特殊", "active"),
                    PhaseStep("終了", "pending"),
                ],
            )

        if not self.dice_rolled:
            return (
                "ターン進行",
                "まずダイスを振る",
                [
                    PhaseStep("ダイス", "active"),
                    PhaseStep("行動", "pending"),
                    PhaseStep("終了", "pending"),
                ],
            )

        if self.action_mode is not None:
            return (
                "ターン進行",
                "建設先を選択中",
                [
                    PhaseStep("ダイス", "complete"),
                    PhaseStep("行動", "active"),
                    PhaseStep("終了", "pending"),
                ],
            )

        return (
            "ターン進行",
            "行動するか手番終了",
            [
                PhaseStep("ダイス", "complete"),
                PhaseStep("行動", "active"),
                PhaseStep("終了", "pending"),
            ],
        )

    def get_progress_header_data(self):
        tracker_title, _, tracker_steps = self.get_phase_tracker_data()
        guidance = self.get_side_panel_guidance()
        instruction = guidance[0] if guidance else "ゲームを進めてください。"
        actor = None

        if self.special_phase == "player_handoff":
            actor = self.handoff_player
        elif (
            self.phase == "initial"
            and self.initial_dice_phase
            and self.initial_dice_contenders
        ):
            actor = self.initial_dice_contenders[self.initial_player_index]
        elif self.phase == "initial" and self.initial_placement_order:
            actor = self.initial_placement_order[self.initial_player_index]
        elif self.phase == "main":
            if self.is_domestic_trade_phase():
                actor = self.get_domestic_trade_actor()
            else:
                actor = (
                    self.discard_player
                    if self.special_phase == "discard"
                    else self.get_current_player()
                )

        if self.special_phase == "player_handoff" and actor is not None:
            title = f"{actor.name}へ画面を渡してください"
        elif self.is_domestic_trade_phase() and actor is not None:
            if self.special_phase in (
                "domestic_trade_handoff",
                "domestic_trade_response",
                "domestic_trade_edit",
            ) and (
                self.domestic_trade_is_counter
                or self.special_phase != "domestic_trade_edit"
            ):
                title = f"{actor.name}の回答 — 交渉"
            elif self.special_phase in (
                "domestic_trade_counter_handoff",
                "domestic_trade_counter_response",
            ):
                title = f"{actor.name}の確認 — 交渉"
            else:
                title = f"{actor.name}の提案 — 交渉"
        elif self.phase == "finished":
            title = (
                f"ゲーム終了 — {self.winner.name}の勝利"
                if self.winner is not None
                else "ゲーム終了"
            )
        elif actor is None:
            title = tracker_title
        elif self.phase == "initial":
            title = f"{tracker_title} — {actor.name}"
        elif actor.is_ai:
            title = (
                f"{actor.name}（{self.get_public_player_ai_personality_label(actor)}）の手番"
            )
        else:
            title = f"あなたの手番 — {actor.name}"

        return {
            "title": title,
            "instruction": instruction,
            "steps": tracker_steps,
            "actor_color": actor.color if actor is not None else COLORS["PANEL_BORDER"],
            "is_ai": bool(actor is not None and actor.is_ai),
        }

    def get_guidance_state(self):
        return GuidanceState(
            phase=self.phase,
            initial_dice_phase=self.initial_dice_phase,
            waiting_for_road=self.waiting_for_road,
            initial_round=self.initial_round,
            special_phase=self.special_phase,
            dice_rolled=self.dice_rolled,
            action_mode=self.action_mode,
            show_seed_input_hint=self.seed_input_active,
            victory_point_target=self.victory_point_target,
            discard_player_name=self.discard_player.name
            if self.discard_player is not None
            else None,
            discard_remaining=self.discard_remaining,
            resource_selection_remaining=self.resource_selection_remaining,
            free_roads_remaining=self.free_roads_remaining,
        )

    def get_help_panel_content(self):
        return build_help_panel_content(self.get_guidance_state())

    def get_action_mode_guidance(self, player):
        if player is None or self.action_mode is None:
            return []

        if self.action_mode == "road":
            road_cost, _waived = self.get_effective_road_cost(player)
            preview = self.get_build_preview(
                "街道", player, road_cost, player.roads_remaining > 0
            )
            candidates = self.get_buildable_road_edges(player)
            return build_action_mode_guidance("road", preview, len(candidates))

        if self.action_mode == "settlement":
            preview = self.get_build_preview(
                "開拓地",
                player,
                BUILD_COSTS["settlement"],
                player.settlements_remaining > 0,
            )
            candidates = self.get_buildable_settlement_nodes(player)
            return build_action_mode_guidance("settlement", preview, len(candidates))

        if self.action_mode == "city":
            preview = self.get_build_preview(
                "都市", player, BUILD_COSTS["city"], player.cities_remaining > 0
            )
            candidates = self.get_buildable_city_nodes(player)
            return build_action_mode_guidance("city", preview, len(candidates))

        return []

    def get_side_panel_guidance(self):
        player = self.get_current_player()
        if self.special_phase == "player_handoff":
            return [
                f"次: {self.handoff_player.name} に画面を渡す",
                f"「{self.handoff_player.name} の画面を開く」は本人が押してください。",
            ]
        if self.is_domestic_trade_phase():
            return self.get_domestic_trade_guidance()
        if self.is_ai_input_locked():
            active_ai = (
                self.discard_player if self.special_phase == "discard" else player
            )
            if active_ai is not None:
                status_title = self.ai_status.get("title", "次の行動を検討中")
                if self.ai_status.get("player_name") != active_ai.name:
                    status_title = "次の行動を検討中"
                detail = self.ai_status.get("detail", "")
                secondary = (
                    detail or "盤面・公開情報・自分の手札から合法手を比較します。"
                )
                personality_label = self.get_public_player_ai_personality_label(
                    active_ai
                )
                return [
                    f"{active_ai.name}（{personality_label}）: {status_title}",
                    secondary,
                ]
        action_guidance = self.get_action_mode_guidance(player)
        if action_guidance:
            return build_side_panel_guidance(
                self.get_guidance_state(), action_guidance, []
            )

        actionable_actions = self.get_actionable_button_actions(player) - {
            "cancel_action"
        }
        action_labels = {
            "mode_road": "街道",
            "mode_settlement": "開拓地",
            "mode_city": "都市",
            "buy_dev": "発展購入",
            "bank_trade": "銀行交易",
            "domestic_trade": "交渉",
            "use_knight": "騎士",
            "use_road_building": "街道建設",
            "use_year_of_plenty": "収穫",
            "use_monopoly": "独占",
            "end_turn": "手番終了",
        }
        highlighted_labels = [
            action_labels[action]
            for action in action_labels
            if action in actionable_actions
        ]
        return build_side_panel_guidance(
            self.get_guidance_state(), action_guidance, highlighted_labels
        )

    def get_initial_dice_history_text(self, player):
        return " -> ".join(
            str(value) for value in self.initial_dice_histories.get(player.name, [])
        )

    def has_active_dice_animation(self):
        return self.pending_dice_context is not None and self.dice_overlay.is_active

    def start_dice_animation(self, context, dice_values, player_name, title):
        dice_pair = tuple(int(value) for value in dice_values)
        if len(dice_pair) != 2 or any(value < 1 or value > 6 for value in dice_pair):
            raise ValueError("ダイスは1〜6の2個で指定してください。")
        self.last_dice_pair = dice_pair
        if self.headless:
            dice_roll = sum(dice_pair)
            if context == "initial":
                self.resolve_initial_key_roll(dice_roll)
            elif context == "main":
                self.resolve_main_dice_roll(dice_roll)
            return
        self.pending_dice_context = context
        self.pending_dice_roll = sum(dice_pair)
        self.pending_dice_player_name = player_name
        self.play_sound("dice")
        subtitle = f"{player_name} が振っています" if player_name else ""
        self.dice_overlay.start(dice_pair, title, subtitle)

    def update_dice_animation(self):
        if self.pending_dice_context is None:
            return
        if not self.dice_overlay.update(pygame.time.get_ticks()):
            return

        context = self.pending_dice_context
        dice_roll = self.pending_dice_roll
        self.pending_dice_context = None
        self.pending_dice_roll = None
        self.pending_dice_player_name = ""

        if context == "initial":
            self.resolve_initial_key_roll(dice_roll)
        elif context == "main":
            self.resolve_main_dice_roll(dice_roll)

    def has_bank_trade_option(self, player):
        if player is None:
            return False
        trade_rates = self.get_trade_rates(player)
        return any(
            player.available_resource_count(give_resource)
            >= trade_rates[give_resource]
            and any(
                receive_resource != give_resource
                and self.bank.available(receive_resource) > 0
                for receive_resource in RESOURCE_TYPES
            )
            for give_resource in trade_rates
        )

    def get_actionable_button_actions(self, player):
        actions = set()
        if player is None:
            return actions

        if self.special_phase == "player_handoff":
            actions.add("player_handoff_reveal")
            return actions

        if self.phase == "initial" and self.initial_dice_phase:
            actions.add("initial_roll")
            return actions

        if self.phase != "main" or self.winner is not None:
            return actions

        if self.is_domestic_trade_phase():
            actions.add("domestic_trade_cancel")
            if self.special_phase == "domestic_trade_partner":
                actions.add("domestic_trade_broadcast")
                actions.update(
                    f"domestic_trade_partner_{index}"
                    for index, other in enumerate(self.players)
                    if other is not self.get_current_player()
                    and other.available_resource_total() > 0
                )
            elif self.special_phase == "domestic_trade_edit":
                actions.update(
                    {
                        "domestic_trade_edit_give",
                        "domestic_trade_edit_receive",
                        "domestic_trade_submit",
                    }
                )
            elif self.special_phase in (
                "domestic_trade_handoff",
                "domestic_trade_counter_handoff",
            ):
                actions.add("domestic_trade_reveal")
            elif self.special_phase == "domestic_trade_response":
                actions.update(
                    {
                        "domestic_trade_accept",
                        "domestic_trade_counter",
                        "domestic_trade_reject",
                    }
                )
            elif self.special_phase == "domestic_trade_counter_response":
                actions.update({"domestic_trade_accept", "domestic_trade_reject"})
            return actions

        if self.special_phase == "bank_trade_give":
            trade_rates = self.get_trade_rates(player)
            for resource_type, required in trade_rates.items():
                if player.available_resource_count(resource_type) >= required and any(
                    receive_resource != resource_type
                    and self.bank.available(receive_resource) > 0
                    for receive_resource in RESOURCE_TYPES
                ):
                    actions.add(f"trade_resource_{resource_type.name}")
            actions.add("cancel_action")
            return actions

        if self.special_phase == "bank_trade_receive":
            for resource_type in RESOURCE_TYPES:
                if resource_type == self.bank_trade_give_resource:
                    continue
                if self.bank.available(resource_type) <= 0:
                    continue
                actions.add(f"trade_resource_{resource_type.name}")
            actions.add("cancel_action")
            return actions

        if self.special_phase == "road_building":
            return actions

        if self.special_phase in ("discard", "year_of_plenty", "monopoly"):
            actions.update(
                self.get_resource_button_action(resource_type)
                for resource_type in self.get_selectable_resources()
            )
            return actions

        if self.special_phase is not None:
            return actions

        if not self.development_card_used_this_turn:
            if player.has_playable_development_card(DevelopmentCardType.KNIGHT):
                actions.add("use_knight")
            if (
                player.has_playable_development_card(DevelopmentCardType.ROAD_BUILDING)
                and player.roads_remaining > 0
                and self.has_legal_road_placement(player)
            ):
                actions.add("use_road_building")
            if (
                player.has_playable_development_card(DevelopmentCardType.YEAR_OF_PLENTY)
                and self.bank.total_cards() > 0
            ):
                actions.add("use_year_of_plenty")
            if player.has_playable_development_card(DevelopmentCardType.MONOPOLY):
                actions.add("use_monopoly")

        if not self.dice_rolled:
            actions.add("roll_dice")
            return actions

        if self.action_mode is not None:
            actions.add("cancel_action")

        if self.get_buildable_road_edges(player):
            actions.add("mode_road")
        if self.get_buildable_settlement_nodes(player):
            actions.add("mode_settlement")
        if self.get_buildable_city_nodes(player):
            actions.add("mode_city")
        if self.development_deck and player.can_afford(BUILD_COSTS["development"]):
            actions.add("buy_dev")
        if self.has_bank_trade_option(player):
            actions.add("bank_trade")
        if self.has_domestic_trade_option(player):
            actions.add("domestic_trade")

        actions.add("end_turn")
        return actions

    def build_buttons(self):
        if self.is_pre_game_settings_open():
            return []
        buttons = []
        panel_padding = 16
        base_x = SIDE_PANEL_X + panel_padding
        base_y = 216
        available_width = SIDE_PANEL_WIDTH - panel_padding * 2
        button_width = int((available_width - 12) / 2)
        button_height = 36
        gap_x = 12
        gap_y = 10
        actionable_actions = self.get_actionable_button_actions(
            self.get_current_player()
        )

        def add(
            action, label, row, col, enabled=True, selected=False, highlighted=False
        ):
            rect = pygame.Rect(
                base_x + col * (button_width + gap_x),
                base_y + row * (button_height + gap_y),
                button_width,
                button_height,
            )
            buttons.append(
                UIButton(
                    action,
                    label,
                    rect,
                    enabled=enabled,
                    selected=selected,
                    highlighted=highlighted,
                )
            )

        def add_custom(
            action,
            label,
            x,
            y,
            width,
            height,
            enabled=True,
            selected=False,
            highlighted=False,
        ):
            rect = pygame.Rect(x, y, width, height)
            buttons.append(
                UIButton(
                    action,
                    label,
                    rect,
                    enabled=enabled,
                    selected=selected,
                    highlighted=highlighted,
                )
            )

        if self.replay_mode:
            total_frames = (
                len(self.replay_archive.frames)
                if self.replay_archive is not None
                else 0
            )
            at_first = self.replay_index <= 0
            at_last = self.replay_index >= total_frames - 1
            add("replay_first", "|◀ 先頭", 0, 0, enabled=not at_first)
            add("replay_previous", "◀ 前へ", 0, 1, enabled=not at_first)
            add(
                "replay_play_pause",
                "Ⅱ 停止" if self.replay_playing else "▶ 自動再生",
                1,
                0,
                highlighted=True,
            )
            add("replay_next", "次へ ▶", 1, 1, enabled=not at_last)
            add("replay_last", "末尾 ▶|", 2, 0, enabled=not at_last)
            add("replay_exit", "終了 Esc", 2, 1)
            add_custom(
                "replay_toggle_reveal",
                "全員の手札を隠す（V）"
                if self.replay_reveal_all
                else "全員の手札を公開（V）",
                base_x,
                base_y + 3 * (button_height + gap_y),
                available_width,
                button_height,
                selected=self.replay_reveal_all,
            )
            return buttons

        current_player = self.get_current_player()
        if self.special_phase == "player_handoff":
            add_custom(
                "player_handoff_reveal",
                f"{self.handoff_player.name} の画面を開く",
                base_x,
                base_y,
                available_width,
                button_height,
                highlighted=True,
            )
            return buttons

        if self.is_ai_input_locked():
            add_custom(
                "ai_speed_cycle",
                f"AI速度: {self.get_ai_speed_label()}（A）",
                base_x,
                base_y,
                available_width,
                button_height,
                highlighted=self.ai_paused,
            )
            return buttons

        if self.phase == "initial" and self.initial_dice_phase:
            # Nine compact setup rows fit above the summary at 1200x800.
            button_height = 32
            gap_y = 6
            settings_unlocked = self.can_edit_pre_game_settings()
            add(
                "player_count_2",
                "2人（簡易）",
                0,
                0,
                enabled=settings_unlocked,
                selected=len(self.players) == 2,
            )
            add(
                "player_count_3",
                "3人",
                0,
                1,
                enabled=settings_unlocked,
                selected=len(self.players) == 3,
            )
            add(
                "player_count_4",
                "4人",
                1,
                0,
                enabled=settings_unlocked,
                selected=len(self.players) == 4,
            )
            add(
                "initial_roll",
                "初期ダイス",
                1,
                1,
                enabled=bool(self.players),
                highlighted="initial_roll" in actionable_actions,
            )
            add(
                "board_mode_constrained",
                "制約付き",
                2,
                0,
                enabled=settings_unlocked,
                selected=self.board_mode == "constrained",
            )
            add(
                "board_mode_fully_random",
                "公式ランダム",
                2,
                1,
                enabled=settings_unlocked,
                selected=self.board_mode == "fully_random",
            )
            seed_value = self.board_seed_text or "入力..."
            seed_label = f"seed: {seed_value}"
            show_cursor = (
                self.seed_input_active and (pygame.time.get_ticks() // 450) % 2 == 0
            )
            if show_cursor:
                seed_label = f"seed: {seed_value}|"
            add_custom(
                "seed_input_focus",
                seed_label,
                base_x,
                base_y + 3 * (button_height + gap_y),
                available_width,
                button_height,
                enabled=settings_unlocked,
                selected=self.seed_input_active,
            )
            add("seed_apply", "seed反映", 4, 0, enabled=settings_unlocked)
            add("seed_randomize", "再生成", 4, 1, enabled=settings_unlocked)
            compact_gap = 8
            compact_width = int((available_width - compact_gap * 2) / 3)
            compact_y = base_y + 5 * (button_height + gap_y)
            add_custom(
                "ai_count_cycle",
                f"AI {self.ai_player_count}人",
                base_x,
                compact_y,
                compact_width,
                button_height,
                enabled=settings_unlocked,
                selected=self.ai_player_count > 0,
            )
            add_custom(
                "ai_speed_cycle",
                f"速度 {self.get_ai_speed_compact_label()}",
                base_x + compact_width + compact_gap,
                compact_y,
                compact_width,
                button_height,
                selected=self.get_ai_speed_label() != "標準",
            )
            add_custom(
                "ai_personality_cycle",
                f"性格 {self.get_ai_personality_mode_compact_label()}",
                base_x + (compact_width + compact_gap) * 2,
                compact_y,
                compact_width,
                button_height,
                enabled=settings_unlocked,
                selected=self.ai_personality_mode != STANDARD,
            )
            add(
                "victory_target_decrease",
                f"勝利点 −（{self.victory_point_target}）",
                6,
                0,
                enabled=settings_unlocked
                and self.victory_point_target > MIN_VICTORY_POINT_TARGET,
            )
            add(
                "victory_target_increase",
                f"勝利点 ＋（{self.victory_point_target}）",
                6,
                1,
                enabled=settings_unlocked
                and self.victory_point_target < MAX_VICTORY_POINT_TARGET,
            )
            add_custom(
                "pre_game_settings_open",
                "詳細設定: カスタムマップ・ハウスルール",
                base_x,
                base_y + 7 * (button_height + gap_y),
                available_width,
                button_height,
                enabled=settings_unlocked,
                highlighted=(
                    self.board_mode == "custom"
                    or self.house_rules != HouseRules.standard()
                ),
            )
            replay_path = self.latest_replay_path
            add(
                "lan_lobby_open",
                "LAN対戦",
                8,
                0,
                enabled=settings_unlocked,
                highlighted=True,
            )
            add(
                "replay_open",
                "直前をリプレイ" if replay_path is not None else "リプレイなし",
                8,
                1,
                enabled=replay_path is not None,
            )
            return buttons

        if self.phase == "finished":
            add_custom(
                "replay_open",
                "対局をリプレイ",
                base_x,
                base_y,
                available_width,
                button_height,
                enabled=self.replay_archive is not None,
                highlighted=self.replay_archive is not None,
            )
            add_custom(
                "restart_same_board",
                "同じ盤面でもう一度",
                base_x,
                base_y + button_height + gap_y,
                available_width,
                button_height,
            )
            add_custom(
                "restart_new_board",
                "新しい盤面で遊ぶ",
                base_x,
                base_y + 2 * (button_height + gap_y),
                available_width,
                button_height,
            )
            return buttons
        if self.phase != "main" or self.winner is not None:
            return buttons

        if self.special_phase == "domestic_trade_partner":
            partners = [
                (index, player)
                for index, player in enumerate(self.players)
                if player is not current_player
                and player.available_resource_total() > 0
            ]
            add_custom(
                "domestic_trade_broadcast",
                f"全員に募集（{len(partners)}人）",
                base_x,
                base_y,
                available_width,
                button_height,
                highlighted=True,
            )
            for slot, (index, partner) in enumerate(partners):
                profile = self.get_public_production_profile(partner)
                add_custom(
                    f"domestic_trade_partner_{index}",
                    f"{partner.name}｜手札{partner.total_resource_count()}枚｜生産 {profile}",
                    base_x,
                    base_y + (slot + 1) * (button_height + gap_y),
                    available_width,
                    button_height,
                    highlighted=True,
                )
            cancel_y = base_y + (len(partners) + 1) * (button_height + gap_y)
            add_custom(
                "domestic_trade_cancel",
                "交渉をやめる",
                base_x,
                cancel_y,
                available_width,
                button_height,
            )
            return buttons

        if self.special_phase == "domestic_trade_edit":
            add(
                "domestic_trade_edit_give",
                "渡す資源",
                0,
                0,
                selected=self.domestic_trade_edit_side == "give",
            )
            add(
                "domestic_trade_edit_receive",
                "欲しい資源",
                0,
                1,
                selected=self.domestic_trade_edit_side == "receive",
            )
            side = self.domestic_trade_edit_side
            bundle = (
                self.domestic_trade_give
                if side == "give"
                else self.domestic_trade_receive
            )
            rows_y = base_y + button_height + 10
            minus_width = 42
            plus_width = 42
            row_gap = 6
            value_x = base_x + minus_width + 8
            value_width = available_width - minus_width - plus_width - 16
            for row, resource_type in enumerate(RESOURCE_TYPES):
                row_y = rows_y + row * (34 + row_gap)
                amount = bundle[resource_type]
                limit = self.get_domestic_trade_quantity_limit(side, resource_type)
                add_custom(
                    f"domestic_trade_adjust_{side}_{resource_type.name}_minus",
                    "−",
                    base_x,
                    row_y,
                    minus_width,
                    34,
                    enabled=amount > 0,
                )
                add_custom(
                    f"domestic_trade_count_{side}_{resource_type.name}",
                    f"{RESOURCE_LABELS[resource_type]}  × {amount}",
                    value_x,
                    row_y,
                    value_width,
                    34,
                    enabled=False,
                )
                add_custom(
                    f"domestic_trade_adjust_{side}_{resource_type.name}_plus",
                    "+",
                    value_x + value_width + 8,
                    row_y,
                    plus_width,
                    34,
                    enabled=amount < limit,
                )
            action_y = rows_y + len(RESOURCE_TYPES) * (34 + row_gap) + 6
            if self.domestic_trade_is_counter:
                submit_label = "条件変更を送る"
            elif self.domestic_trade_is_broadcast:
                submit_label = "全員に募集"
            else:
                submit_label = "提案を送る"
            add_custom(
                "domestic_trade_submit",
                submit_label,
                base_x,
                action_y,
                button_width,
                button_height,
                enabled=self.validate_domestic_trade_terms()[0],
                highlighted=self.validate_domestic_trade_terms()[0],
            )
            add_custom(
                "domestic_trade_cancel",
                "交渉終了",
                base_x + button_width + gap_x,
                action_y,
                button_width,
                button_height,
            )
            return buttons

        if self.special_phase in (
            "domestic_trade_handoff",
            "domestic_trade_counter_handoff",
        ):
            actor = self.get_domestic_trade_actor()
            add_custom(
                "domestic_trade_reveal",
                f"{actor.name} の回答画面を開く",
                base_x,
                base_y,
                available_width,
                button_height,
                highlighted=True,
            )
            return buttons

        if self.special_phase == "domestic_trade_response":
            add_custom(
                "domestic_trade_accept",
                "この条件で承諾",
                base_x,
                base_y,
                available_width,
                button_height,
                enabled=self.can_execute_domestic_trade(),
                highlighted=self.can_execute_domestic_trade(),
            )
            add("domestic_trade_counter", "条件変更", 1, 0)
            add("domestic_trade_reject", "拒否", 1, 1)
            return buttons

        if self.special_phase == "domestic_trade_counter_response":
            add_custom(
                "domestic_trade_accept",
                "変更条件を承諾",
                base_x,
                base_y,
                available_width,
                button_height,
                enabled=self.can_execute_domestic_trade(),
                highlighted=self.can_execute_domestic_trade(),
            )
            add_custom(
                "domestic_trade_reject",
                "変更条件を拒否",
                base_x,
                base_y + button_height + gap_y,
                available_width,
                button_height,
            )
            return buttons

        human_discard_pending = (
            self.special_phase == "discard"
            and self.discard_player is not None
            and not self.discard_player.is_ai
        )
        if (
            current_player is not None
            and current_player.is_ai
            and not human_discard_pending
        ):
            return buttons

        development_actions = []
        if current_player is not None and not self.development_card_used_this_turn:
            if current_player.has_playable_development_card(DevelopmentCardType.KNIGHT):
                development_actions.append(("use_knight", "騎士"))
            if current_player.has_playable_development_card(
                DevelopmentCardType.ROAD_BUILDING
            ):
                development_actions.append(("use_road_building", "街道建設"))
            if current_player.has_playable_development_card(
                DevelopmentCardType.YEAR_OF_PLENTY
            ):
                development_actions.append(("use_year_of_plenty", "収穫"))
            if current_player.has_playable_development_card(
                DevelopmentCardType.MONOPOLY
            ):
                development_actions.append(("use_monopoly", "独占"))

        if self.special_phase == "road_building":
            return buttons

        if self.special_phase in ("bank_trade_give", "bank_trade_receive"):
            add_custom(
                "cancel_action",
                "取消",
                base_x,
                base_y,
                available_width,
                button_height,
                enabled=True,
                highlighted="cancel_action" in actionable_actions,
            )
            selector_x = base_x
            selector_y = base_y + button_height + 14
            selector_width = int((available_width - 16) / 3)
            selector_height = 34
            selector_gap_x = 8
            selector_gap_y = 8
            for index, resource_type in enumerate(
                [
                    ResourceType.WOOD,
                    ResourceType.SHEEP,
                    ResourceType.WHEAT,
                    ResourceType.BRICK,
                    ResourceType.ORE,
                ]
            ):
                col = index % 3
                row = index // 3
                selected = self.bank_trade_give_resource == resource_type
                label = RESOURCE_LABELS[resource_type]
                add_custom(
                    f"trade_resource_{resource_type.name}",
                    label,
                    selector_x + col * (selector_width + selector_gap_x),
                    selector_y + row * (selector_height + selector_gap_y),
                    selector_width,
                    selector_height,
                    enabled=f"trade_resource_{resource_type.name}"
                    in actionable_actions,
                    selected=selected and self.special_phase == "bank_trade_receive",
                    highlighted=f"trade_resource_{resource_type.name}"
                    in actionable_actions,
                )
            return buttons

        if self.special_phase in ("discard", "year_of_plenty", "monopoly"):
            selector_width = int((available_width - 16) / 3)
            selector_height = 40
            selector_gap_x = 8
            selector_gap_y = 8
            selectable_resources = set(self.get_selectable_resources())
            resource_order = [
                ResourceType.WOOD,
                ResourceType.SHEEP,
                ResourceType.WHEAT,
                ResourceType.BRICK,
                ResourceType.ORE,
            ]
            for index, resource_type in enumerate(resource_order):
                col = index % 3
                row = index // 3
                action = self.get_resource_button_action(resource_type)
                label = f"{index + 1}  {RESOURCE_LABELS[resource_type]}"
                if self.special_phase == "discard" and self.discard_player is not None:
                    label += f" ×{self.discard_player.resources.get(resource_type, 0)}"
                add_custom(
                    action,
                    label,
                    base_x + col * (selector_width + selector_gap_x),
                    base_y + row * (selector_height + selector_gap_y),
                    selector_width,
                    selector_height,
                    enabled=resource_type in selectable_resources,
                    highlighted=action in actionable_actions,
                )
            return buttons

        if self.special_phase is not None:
            return buttons

        if not self.dice_rolled:
            add_custom(
                "roll_dice",
                "ダイス",
                base_x,
                base_y,
                available_width,
                button_height,
                enabled=True,
                highlighted="roll_dice" in actionable_actions,
            )
            for index, (action, label) in enumerate(development_actions):
                add(
                    action,
                    label,
                    1 + index // 2,
                    index % 2,
                    enabled=action in actionable_actions,
                )
            return buttons

        grid_base_y = base_y
        if self.action_mode is not None:
            add_custom(
                "cancel_action",
                "選択を取り消す",
                base_x,
                grid_base_y,
                available_width,
                button_height,
                enabled=True,
                highlighted="cancel_action" in actionable_actions,
            )
            grid_base_y += button_height + gap_y

        def add_grid(
            action, label, slot, enabled=True, selected=False, highlighted=False
        ):
            row = slot // 2
            col = slot % 2
            rect = pygame.Rect(
                base_x + col * (button_width + gap_x),
                grid_base_y + row * (button_height + gap_y),
                button_width,
                button_height,
            )
            buttons.append(
                UIButton(
                    action,
                    label,
                    rect,
                    enabled=enabled,
                    selected=selected,
                    highlighted=highlighted,
                )
            )

        slot = 0
        add_grid(
            "mode_road",
            "街道 木+土",
            slot,
            enabled="mode_road" in actionable_actions,
            selected=self.action_mode == "road",
        )
        slot += 1
        add_grid(
            "mode_settlement",
            "開拓 木土羊麦",
            slot,
            enabled="mode_settlement" in actionable_actions,
            selected=self.action_mode == "settlement",
        )
        slot += 1
        add_grid(
            "mode_city",
            "都市 麦2鉄3",
            slot,
            enabled="mode_city" in actionable_actions,
            selected=self.action_mode == "city",
        )
        slot += 1
        add_grid(
            "buy_dev",
            "発展 羊麦鉄",
            slot,
            enabled="buy_dev" in actionable_actions,
        )
        slot += 1
        add_grid(
            "bank_trade", "銀行交易", slot, enabled="bank_trade" in actionable_actions
        )
        slot += 1
        add_grid(
            "domestic_trade",
            "交渉",
            slot,
            enabled="domestic_trade" in actionable_actions,
        )
        slot += 1
        add_grid(
            "end_turn",
            "手番終了",
            slot,
            enabled=True,
            highlighted=self.action_mode is None,
        )
        slot += 1

        for action, label in development_actions:
            add_grid(
                action,
                label,
                slot,
                enabled=action in actionable_actions,
            )
            slot += 1

        return buttons

    def find_clicked_button(self, pos):
        for button in self.buttons:
            if button.rect.collidepoint(pos) and button.enabled:
                return button
        return None

    def format_resource_bundle(self, bundle):
        parts = [
            f"{RESOURCE_LABELS[resource_type]}{amount}"
            for resource_type in RESOURCE_TYPES
            if (amount := bundle.get(resource_type, 0)) > 0
        ]
        return "・".join(parts) if parts else "なし"

    def get_domestic_trade_actor(self):
        if not self.is_domestic_trade_phase():
            return None
        active_player = self.get_current_player()
        if self.special_phase in ("domestic_trade_handoff", "domestic_trade_response"):
            return self.domestic_trade_partner
        if (
            self.special_phase == "domestic_trade_edit"
            and self.domestic_trade_is_counter
        ):
            return self.domestic_trade_partner
        if self.special_phase in (
            "domestic_trade_counter_handoff",
            "domestic_trade_counter_response",
        ):
            return active_player
        return active_player

    def get_domestic_trade_summary(self):
        give = self.format_resource_bundle(self.domestic_trade_give)
        receive = self.format_domestic_trade_receive()
        return f"渡す {give} / 欲しい {receive}"

    def get_domestic_trade_compact_summary(self):
        give = self.format_resource_bundle(self.domestic_trade_give)
        receive = self.format_domestic_trade_receive()
        return f"{give} → {receive}"

    def format_domestic_trade_receive(self):
        if self.domestic_trade_receive_operator != "or":
            return self.format_resource_bundle(self.domestic_trade_receive)
        choices = [
            f"{RESOURCE_LABELS[resource_type]}{amount}"
            for resource_type in RESOURCE_TYPES
            if (amount := self.domestic_trade_receive.get(resource_type, 0)) > 0
        ]
        return " または ".join(choices) if choices else "なし"

    def get_domestic_trade_broadcast_progress(self):
        total = len(self.domestic_trade_broadcast_responders)
        if total <= 0 or self.domestic_trade_broadcast_index < 0:
            return f"全員募集（{total}人）"
        return f"全員募集 {self.domestic_trade_broadcast_index + 1}/{total}"

    def get_domestic_trade_guidance(self):
        partner_name = (
            self.domestic_trade_partner.name
            if self.domestic_trade_partner is not None
            else "相手"
        )
        if self.special_phase == "domestic_trade_partner":
            return [
                "次: 交渉相手または「全員に募集」を選ぶ",
                "生産傾向と直近公開獲得は推測用。現在の手札内訳は非公開です。",
            ]
        if self.special_phase == "domestic_trade_edit":
            if self.domestic_trade_is_counter:
                action = "条件変更を送る"
            elif self.domestic_trade_is_broadcast:
                action = "全員に募集"
            else:
                action = "提案を送る"
            if self.domestic_trade_partner is not None:
                public_summary = self.get_trade_partner_public_summary(
                    self.domestic_trade_partner
                )
            else:
                public_summary = self.get_domestic_trade_compact_summary()
            return [f"次: 資源と枚数を決めて「{action}」", public_summary]
        if self.special_phase == "domestic_trade_handoff":
            prefix = (
                f"{self.get_domestic_trade_broadcast_progress()} / "
                if self.domestic_trade_is_broadcast
                else ""
            )
            return [
                f"次: {prefix}{partner_name} に画面を渡す",
                "手札の内訳を隠したまま回答画面へ進みます。",
            ]
        if self.special_phase == "domestic_trade_response":
            return [
                "次: 承諾・条件変更・拒否を選ぶ",
                self.get_domestic_trade_compact_summary(),
            ]
        if self.special_phase == "domestic_trade_counter_handoff":
            return [
                "次: 手番プレイヤーに画面を戻す",
                "変更された条件への回答画面を開きます。",
            ]
        if self.special_phase == "domestic_trade_counter_response":
            return [
                "次: 変更条件を承諾または拒否する",
                self.get_domestic_trade_compact_summary(),
            ]
        return ["交渉を進めてください", self.get_domestic_trade_compact_summary()]

    def get_domestic_trade_subtitle(self):
        active_player = self.get_current_player()
        partner = self.domestic_trade_partner
        if self.domestic_trade_is_broadcast and partner is None:
            return f"全員募集: {active_player.name} / {self.get_domestic_trade_compact_summary()}"
        if partner is None:
            return f"国内交易: {active_player.name} が相手を選択"
        if self.domestic_trade_is_broadcast:
            prefix = self.get_domestic_trade_broadcast_progress()
            if self.domestic_trade_is_counter:
                prefix += "・条件変更"
        else:
            prefix = "条件変更" if self.domestic_trade_is_counter else "提案"
        return (
            f"{prefix}: {active_player.name} ⇄ {partner.name}（手札{partner.total_resource_count()}枚）"
            f" / {self.get_domestic_trade_compact_summary()}"
        )

    def has_domestic_trade_option(self, player):
        if player is None or player.available_resource_total() <= 0:
            return False
        return any(
            other is not player and other.available_resource_total() > 0
            for other in self.players
        )

    def get_domestic_trade_eligible_partners(self, active_player=None):
        active_player = active_player or self.get_current_player()
        if active_player is None:
            return []
        if active_player in self.turn_order:
            active_index = self.turn_order.index(active_player)
            ordered_players = (
                self.turn_order[active_index + 1 :] + self.turn_order[:active_index]
            )
        else:
            ordered_players = [
                player for player in self.players if player is not active_player
            ]
        return [
            player
            for player in ordered_players
            if player is not active_player and player.available_resource_total() > 0
        ]

    def start_domestic_trade(self):
        if self.phase != "main" or self.winner is not None:
            return False
        if not self.dice_rolled:
            self.notify_invalid("国内交易はダイスの結果を解決した後に行ってください。")
            return False
        if self.special_phase is not None:
            self.notify_invalid("進行中の処理を完了してから交渉してください。")
            return False
        active_player = self.get_current_player()
        if not self.has_domestic_trade_option(active_player):
            self.notify_invalid("交換可能な資源を持つ交渉相手がいません。")
            return False
        self.action_mode = None
        self.clear_domestic_trade_state()
        self.special_phase = "domestic_trade_partner"
        self.domestic_trade_editor = active_player
        self.add_log(f"{active_player.name} が国内交易の交渉を始めました。")
        return True

    def select_domestic_trade_partner(self, player_index):
        if self.special_phase != "domestic_trade_partner":
            return False
        if not 0 <= player_index < len(self.players):
            return False
        active_player = self.get_current_player()
        partner = self.players[player_index]
        if partner is active_player:
            self.notify_invalid("自分自身とは交易できません。")
            return False
        if partner.available_resource_total() <= 0:
            self.notify_invalid(f"{partner.name} は交換できる資源を持っていません。")
            return False
        self.domestic_trade_partner = partner
        self.domestic_trade_editor = active_player
        self.domestic_trade_edit_side = "give"
        self.special_phase = "domestic_trade_edit"
        return True

    def select_domestic_trade_broadcast(self):
        if self.special_phase != "domestic_trade_partner":
            return False
        active_player = self.get_current_player()
        responders = self.get_domestic_trade_eligible_partners(active_player)
        if not responders:
            self.notify_invalid("募集に回答できるプレイヤーがいません。")
            return False
        self.domestic_trade_is_broadcast = True
        self.domestic_trade_broadcast_responders = responders
        self.domestic_trade_broadcast_index = -1
        self.domestic_trade_broadcast_viewer = active_player
        self.domestic_trade_partner = None
        self.domestic_trade_editor = active_player
        self.domestic_trade_edit_side = "give"
        self.special_phase = "domestic_trade_edit"
        self.add_log(f"{active_player.name} が全員募集の交易条件を編集中です。")
        return True

    def set_domestic_trade_edit_side(self, side):
        if self.special_phase != "domestic_trade_edit" or side not in (
            "give",
            "receive",
        ):
            return False
        self.domestic_trade_edit_side = side
        return True

    def set_domestic_trade_receive_operator(self, operator):
        if (
            self.special_phase != "domestic_trade_edit"
            or operator not in ("and", "or")
            or operator == self.domestic_trade_receive_operator
        ):
            return False
        self.domestic_trade_receive_operator = operator
        return True

    def get_domestic_trade_quantity_limit(self, side, resource_type):
        active_player = self.get_current_player()
        editor = self.domestic_trade_editor
        if side == "give" and editor is active_player:
            return active_player.available_resource_count(resource_type)
        if side == "receive" and editor is self.domestic_trade_partner:
            return self.domestic_trade_partner.available_resource_count(resource_type)
        return BANK_RESOURCE_COUNT

    def adjust_domestic_trade_resource(self, side, resource_type, delta):
        if self.special_phase != "domestic_trade_edit" or side not in (
            "give",
            "receive",
        ):
            return False
        bundle = (
            self.domestic_trade_give if side == "give" else self.domestic_trade_receive
        )
        other_bundle = (
            self.domestic_trade_receive if side == "give" else self.domestic_trade_give
        )
        if delta > 0 and other_bundle.get(resource_type, 0) > 0:
            self.notify_invalid(
                "同じ資源を渡す側と受け取る側の両方には指定できません。"
            )
            return False
        current = bundle.get(resource_type, 0)
        limit = self.get_domestic_trade_quantity_limit(side, resource_type)
        bundle[resource_type] = max(0, min(limit, current + delta))
        return bundle[resource_type] != current

    def player_can_pay_bundle(self, player, bundle):
        return player.can_afford(bundle)

    def get_domestic_trade_receive_branches(self):
        """Return stable, exact receive bundles for the current offer.

        ``and`` keeps the legacy bundle intact.  ``or`` treats every positive
        resource row as a separate alternative; the accepting player must
        explicitly select one of those rows before any cards move.
        """

        if self.domestic_trade_receive_operator == "and":
            return [(None, dict(self.domestic_trade_receive))]
        return [
            (
                resource_type,
                {
                    candidate: (
                        self.domestic_trade_receive[resource_type]
                        if candidate is resource_type
                        else 0
                    )
                    for candidate in RESOURCE_TYPES
                },
            )
            for resource_type in RESOURCE_TYPES
            if self.domestic_trade_receive.get(resource_type, 0) > 0
        ]

    def get_domestic_trade_receive_bundle(self, selected_resource=None):
        if self.domestic_trade_receive_operator == "and":
            return (
                dict(self.domestic_trade_receive)
                if selected_resource is None
                else None
            )
        if selected_resource not in RESOURCE_TYPES:
            return None
        amount = self.domestic_trade_receive.get(selected_resource, 0)
        if amount <= 0:
            return None
        return {
            resource_type: amount if resource_type is selected_resource else 0
            for resource_type in RESOURCE_TYPES
        }

    def validate_domestic_trade_terms(self):
        if (
            sum(self.domestic_trade_give.values()) <= 0
            or sum(self.domestic_trade_receive.values()) <= 0
        ):
            return False, "国内交易では双方が1枚以上の資源を渡す必要があります。"
        if self.domestic_trade_receive_operator not in ("and", "or"):
            return False, "受け取り条件の結合方法が不正です。"
        if (
            self.domestic_trade_receive_operator == "or"
            and sum(
                amount > 0 for amount in self.domestic_trade_receive.values()
            )
            < 2
        ):
            return False, "OR条件では欲しい資源を2種類以上指定してください。"
        if any(
            self.domestic_trade_give[resource_type] > 0
            and self.domestic_trade_receive[resource_type] > 0
            for resource_type in RESOURCE_TYPES
        ):
            return False, "同じ資源を双方の条件には指定できません。"
        editor = self.domestic_trade_editor
        outgoing = (
            self.domestic_trade_give
            if editor is self.get_current_player()
            else self.domestic_trade_receive
        )
        if editor is not None and not self.player_can_pay_bundle(editor, outgoing):
            return False, f"{editor.name} が持っている枚数を超えています。"
        return True, ""

    def can_execute_domestic_trade(self, selected_resource=None):
        active_player = self.get_current_player()
        partner = self.domestic_trade_partner
        receive_bundle = self.get_domestic_trade_receive_bundle(
            selected_resource
        )
        return bool(
            active_player is not None
            and partner is not None
            and receive_bundle is not None
            and self.player_can_pay_bundle(active_player, self.domestic_trade_give)
            and self.player_can_pay_bundle(partner, receive_bundle)
        )

    def restore_domestic_trade_broadcast_terms(self):
        self.domestic_trade_give = dict(self.domestic_trade_broadcast_give)
        self.domestic_trade_receive = dict(self.domestic_trade_broadcast_receive)
        self.domestic_trade_receive_operator = (
            self.domestic_trade_broadcast_receive_operator
        )
        self.domestic_trade_is_counter = False
        self.domestic_trade_editor = self.get_current_player()
        self.domestic_trade_edit_side = "give"

    def evaluate_ai_domestic_trade_branches(self, player, *, responding_partner):
        branches = []
        for selected_resource, receive_bundle in (
            self.get_domestic_trade_receive_branches()
        ):
            if not self.can_execute_domestic_trade(selected_resource):
                continue
            incoming = (
                self.domestic_trade_give
                if responding_partner
                else receive_bundle
            )
            outgoing = (
                receive_bundle
                if responding_partner
                else self.domestic_trade_give
            )
            branches.append((selected_resource, incoming, outgoing))
        return self.ai.choose_domestic_trade_branch(player, branches, game=self)

    def advance_domestic_trade_broadcast(self):
        if not self.domestic_trade_is_broadcast:
            return False

        active_player = self.get_current_player()
        self.restore_domestic_trade_broadcast_terms()
        self.domestic_trade_broadcast_index += 1

        if self.domestic_trade_broadcast_index >= len(
            self.domestic_trade_broadcast_responders
        ):
            previous_viewer = self.domestic_trade_broadcast_viewer
            self.add_log("全員が交易募集を拒否しました。")
            self.record_event(
                f"{active_player.name}の交易募集は不成立",
                "回答者全員が拒否しました",
                level="warning",
                actor=active_player,
            )
            self.finish_domestic_trade(previous_viewer=previous_viewer)
            return True

        partner = self.domestic_trade_broadcast_responders[
            self.domestic_trade_broadcast_index
        ]
        self.domestic_trade_partner = partner
        progress = self.get_domestic_trade_broadcast_progress()
        self.add_log(f"{progress}: {partner.name} に同じ条件を提示しました。")
        if partner.is_ai:
            return self.resolve_ai_domestic_trade_response()
        self.special_phase = "domestic_trade_handoff"
        return True

    def submit_domestic_trade_offer(self):
        if self.special_phase != "domestic_trade_edit":
            return False
        if self.domestic_trade_partner is None and not (
            self.domestic_trade_is_broadcast and self.domestic_trade_broadcast_index < 0
        ):
            return False
        is_valid, message = self.validate_domestic_trade_terms()
        if not is_valid:
            self.notify_invalid(message)
            return False

        active_player = self.get_current_player()
        partner = self.domestic_trade_partner
        summary = self.get_domestic_trade_summary()
        if self.domestic_trade_is_counter:
            self.add_log(f"{partner.name} が条件変更を提示: {summary}")
            self.record_event(
                f"{partner.name}が条件変更",
                summary,
                actor=partner,
            )
            if active_player.is_ai:
                decision, selected_resource = (
                    self.evaluate_ai_domestic_trade_branches(
                        active_player,
                        responding_partner=False,
                    )
                )
                if (
                    decision == "accept"
                    and self.can_execute_domestic_trade(selected_resource)
                ):
                    return self.execute_domestic_trade(selected_resource)
                return self.reject_domestic_trade(
                    active_player,
                    "変更条件を受け入れませんでした",
                )
            self.special_phase = "domestic_trade_counter_handoff"
            return True

        if self.domestic_trade_is_broadcast:
            self.domestic_trade_broadcast_give = dict(self.domestic_trade_give)
            self.domestic_trade_broadcast_receive = dict(self.domestic_trade_receive)
            self.domestic_trade_broadcast_receive_operator = (
                self.domestic_trade_receive_operator
            )
            self.add_log(f"{active_player.name} が全員に交易を募集: {summary}")
            self.record_event(
                f"{active_player.name}が全員に交易を募集",
                summary,
                actor=active_player,
            )
            return self.advance_domestic_trade_broadcast()

        self.add_log(f"{active_player.name} が {partner.name} に交易を提案: {summary}")
        self.record_event(
            f"{active_player.name}が交易を提案",
            f"相手 {partner.name} / {summary}",
            actor=active_player,
        )
        if partner.is_ai:
            return self.resolve_ai_domestic_trade_response()
        self.special_phase = "domestic_trade_handoff"
        return True

    def propose_domestic_trade(self, partner, give, receive):
        if not self.start_domestic_trade():
            return False
        self.domestic_trade_partner = partner
        self.domestic_trade_give.update(give)
        self.domestic_trade_receive.update(receive)
        self.domestic_trade_editor = self.get_current_player()
        self.special_phase = "domestic_trade_edit"
        return self.submit_domestic_trade_offer()

    def resolve_ai_domestic_trade_response(self):
        partner = self.domestic_trade_partner
        active_player = self.get_current_player()
        decision, selected_resource = self.evaluate_ai_domestic_trade_branches(
            partner,
            responding_partner=True,
        )
        if (
            decision == "accept"
            and self.can_execute_domestic_trade(selected_resource)
        ):
            return self.execute_domestic_trade(selected_resource)
        if decision == "counter":
            if selected_resource is not None:
                selected_bundle = self.get_domestic_trade_receive_bundle(
                    selected_resource
                )
                if selected_bundle is None:
                    return self.reject_domestic_trade(
                        partner, "提案を拒否しました"
                    )
                self.domestic_trade_receive = selected_bundle
                self.domestic_trade_receive_operator = "and"
            counter = self.ai.build_domestic_trade_counter(
                active_player,
                partner,
                self.domestic_trade_give,
                self.domestic_trade_receive,
            )
            if counter is not None:
                self.domestic_trade_give, self.domestic_trade_receive = counter
                self.domestic_trade_is_counter = True
                summary = self.get_domestic_trade_summary()
                self.add_log(f"{partner.name} が条件変更を提示: {summary}")
                self.record_event(f"{partner.name}が条件変更", summary, actor=partner)
                if active_player.is_ai:
                    active_decision = self.ai.evaluate_domestic_trade(
                        active_player,
                        incoming=self.domestic_trade_receive,
                        outgoing=self.domestic_trade_give,
                        game=self,
                    )
                    if (
                        active_decision == "accept"
                        and self.can_execute_domestic_trade()
                    ):
                        return self.execute_domestic_trade()
                    return self.reject_domestic_trade(
                        active_player, "変更条件を受け入れませんでした"
                    )
                if self.should_hide_for_handoff(
                    self.domestic_trade_broadcast_viewer,
                    active_player,
                ):
                    self.begin_player_handoff(
                        active_player,
                        return_phase="domestic_trade_counter_response",
                        context="募集の条件変更",
                    )
                else:
                    self.special_phase = "domestic_trade_counter_response"
                return True
        return self.reject_domestic_trade(partner, "提案を拒否しました")

    def reveal_domestic_trade_response(self):
        if self.special_phase == "domestic_trade_handoff":
            self.special_phase = "domestic_trade_response"
            if self.domestic_trade_is_broadcast:
                self.domestic_trade_broadcast_viewer = self.domestic_trade_partner
            return True
        if self.special_phase == "domestic_trade_counter_handoff":
            self.special_phase = "domestic_trade_counter_response"
            if self.domestic_trade_is_broadcast:
                self.domestic_trade_broadcast_viewer = self.get_current_player()
            return True
        return False

    def begin_domestic_trade_counter(self):
        if self.special_phase != "domestic_trade_response":
            return False
        self.domestic_trade_is_counter = True
        self.domestic_trade_editor = self.domestic_trade_partner
        self.domestic_trade_edit_side = "receive"
        self.special_phase = "domestic_trade_edit"
        return True

    def accept_domestic_trade(self, selected_resource=None):
        if self.special_phase not in (
            "domestic_trade_response",
            "domestic_trade_counter_response",
        ):
            return False
        if not self.can_execute_domestic_trade(selected_resource):
            self.notify_invalid(
                "どちらかの手札が条件を満たさないため、この交易は成立しません。"
            )
            return False
        return self.execute_domestic_trade(selected_resource)

    def execute_domestic_trade(self, selected_resource=None):
        if not self.can_execute_domestic_trade(selected_resource):
            return False
        receive_bundle = self.get_domestic_trade_receive_bundle(
            selected_resource
        )
        if receive_bundle is None:
            return False
        previous_viewer = (
            self.domestic_trade_broadcast_viewer
            if self.domestic_trade_is_broadcast
            else self.get_domestic_trade_actor()
        )
        active_player = self.get_current_player()
        partner = self.domestic_trade_partner
        give_text = self.format_resource_bundle(self.domestic_trade_give)
        receive_text = self.format_resource_bundle(receive_bundle)
        for resource_type, amount in self.domestic_trade_give.items():
            if amount <= 0:
                continue
            active_player.remove_resource(resource_type, amount)
            partner.add_resource(resource_type, amount)
        for resource_type, amount in receive_bundle.items():
            if amount <= 0:
                continue
            partner.remove_resource(resource_type, amount)
            active_player.add_resource(resource_type, amount)
        self.record_public_gain(active_player, receive_bundle, "国内交易")
        self.record_public_gain(partner, self.domestic_trade_give, "国内交易")
        self.play_sound("card")
        self.add_log(
            f"国内交易成立: {active_player.name} は {give_text} を渡し、"
            f"{partner.name} から {receive_text} を受け取りました。"
        )
        self.record_event(
            f"{active_player.name}と{partner.name}の交易成立",
            f"{active_player.name}: -{give_text} / +{receive_text}",
            level="success",
            actor=active_player,
        )
        self.match_metrics.record_domestic_trade(
            self.get_match_metric_player_id(active_player),
            self.get_match_metric_player_id(partner),
        )
        self.apply_merchant_festival_bonus((active_player, partner))
        self.finish_domestic_trade(previous_viewer=previous_viewer)
        return True

    def reject_domestic_trade(self, responder=None, reason="提案を拒否しました"):
        responder = responder or self.get_domestic_trade_actor()
        responder_name = responder.name if responder is not None else "相手"
        self.add_log(f"{responder_name}: {reason}。")
        self.record_event(
            f"{responder_name}が交易を拒否",
            reason,
            level="warning",
            actor=responder,
        )
        if self.domestic_trade_is_broadcast:
            if responder is not None and not responder.is_ai:
                self.domestic_trade_broadcast_viewer = responder
            return self.advance_domestic_trade_broadcast()
        self.finish_domestic_trade(previous_viewer=responder)
        return True

    def cancel_domestic_trade(self):
        if not self.is_domestic_trade_phase():
            return False
        if self.special_phase == "domestic_trade_handoff":
            actor = (
                self.domestic_trade_broadcast_viewer
                if self.domestic_trade_is_broadcast
                else self.get_current_player()
            )
        elif self.special_phase == "domestic_trade_counter_handoff":
            actor = self.domestic_trade_partner
        else:
            actor = self.get_domestic_trade_actor() or self.get_current_player()
        self.add_log(f"{actor.name} が国内交易を終了しました。")
        self.finish_domestic_trade(previous_viewer=actor)
        return True

    def finish_domestic_trade(self, previous_viewer=None):
        active_player = self.get_current_player()
        self.special_phase = None
        self.clear_domestic_trade_state()
        if self.should_hide_for_handoff(previous_viewer, active_player):
            self.begin_player_handoff(active_player, context="交渉後の手番")
        if active_player is not None and active_player.is_ai:
            self.schedule_ai_action()

    def start_bank_trade(self):
        if self.phase != "main" or self.winner is not None:
            return
        if not self.dice_rolled:
            self.notify_invalid("交易はダイスを振った後に行ってください。")
            return
        if self.special_phase is not None:
            self.notify_invalid("進行中の特殊処理を先に完了してください。")
            return
        if not self.has_bank_trade_option(self.get_current_player()):
            self.notify_invalid("現在の手札と銀行在庫では港・銀行交易を行えません。")
            return
        self.feedback.clear()
        self.action_mode = None
        self.special_phase = "bank_trade_give"
        self.bank_trade_give_resource = None
        rates = self.get_trade_rates(self.get_current_player())
        rate_text = ", ".join(
            f"{RESOURCE_LABELS[r]}{rate}:1" for r, rate in rates.items()
        )
        self.add_log(f"銀行交易: 先に支払う資源を選んでください。 {rate_text}")

    def select_bank_trade_resource(self, resource_type):
        player = self.get_current_player()
        if player is None:
            return
        trade_rates = self.get_trade_rates(player)

        if self.special_phase == "bank_trade_give":
            required = trade_rates[resource_type]
            if player.available_resource_count(resource_type) < required:
                self.notify_invalid(
                    f"{RESOURCE_LABELS[resource_type]} が不足しています。必要枚数は {required} 枚です。"
                )
                return
            self.bank_trade_give_resource = resource_type
            self.special_phase = "bank_trade_receive"
            self.add_log(
                f"{RESOURCE_LABELS[resource_type]} を {required} 枚支払います。受け取りたい資源を選んでください。"
            )
            return

        if self.special_phase == "bank_trade_receive":
            if resource_type == self.bank_trade_give_resource:
                self.notify_invalid("同じ資源には交換できません。")
                return
            if self.bank.available(resource_type) <= 0:
                self.notify_invalid(
                    f"銀行に {RESOURCE_LABELS[resource_type]} が残っていません。"
                )
                return
            give_resource = self.bank_trade_give_resource
            required = trade_rates[give_resource]
            if player.available_resource_count(give_resource) < required:
                self.notify_invalid("交易に必要な資源が不足しています。")
                return
            self.bank.withdraw(resource_type)
            player.remove_resource(give_resource, required)
            self.bank.deposit(give_resource, required)
            player.add_resource(resource_type)
            self.record_public_gain(player, {resource_type: 1}, "銀行交易")
            self.play_sound("card")
            self.add_log(
                f"{player.name} が銀行交易: {RESOURCE_LABELS[give_resource]} {required} 枚を"
                f" {RESOURCE_LABELS[resource_type]} 1枚に交換しました。"
            )
            self.record_event(
                f"{player.name}が銀行交易",
                f"{RESOURCE_LABELS[give_resource]} -{required} / {RESOURCE_LABELS[resource_type]} +1",
                level="success",
                actor=player,
            )
            self.match_metrics.record_bank_trade(
                self.get_match_metric_player_id(player)
            )
            self.special_phase = None
            self.bank_trade_give_resource = None

    def cancel_selection(self):
        if self.is_domestic_trade_phase():
            self.cancel_domestic_trade()
            return
        if self.special_phase in ("bank_trade_give", "bank_trade_receive"):
            self.special_phase = None
            self.bank_trade_give_resource = None
            self.add_log("銀行交易をキャンセルしました。")
            return
        self.action_mode = None
        self.add_log("行動選択をキャンセルしました。")

    def handle_button_action(self, action):
        if action == "pre_game_settings_open":
            self.open_pre_game_settings()
            return
        if action == "lan_lobby_open":
            self.open_lan_lobby()
            return
        if action == "replay_open":
            self.start_replay()
            return
        if action.startswith("replay_"):
            self.handle_replay_action(action)
            return
        if action.startswith("player_count_"):
            if self.can_edit_pre_game_settings():
                self.configure_players(int(action.rsplit("_", 1)[1]))
            return
        if action == "ai_count_cycle":
            if self.initial_dice_phase:
                self.cycle_ai_player_count()
            return
        if action == "ai_speed_cycle":
            self.cycle_ai_speed()
            return
        if action == "ai_personality_cycle":
            self.cycle_ai_personality_mode()
            return
        if action == "victory_target_decrease":
            self.adjust_victory_point_target(-1)
            return
        if action == "victory_target_increase":
            self.adjust_victory_point_target(1)
            return
        if action == "board_mode_constrained":
            self.set_board_mode("constrained")
            return
        if action == "board_mode_fully_random":
            self.set_board_mode("fully_random")
            return
        if action == "seed_input_focus":
            if self.can_edit_pre_game_settings():
                self.seed_input_active = True
            return
        if action == "seed_apply":
            self.apply_seed_text()
            return
        if action == "seed_randomize":
            self.randomize_board_seed()
            return
        if action == "restart_same_board":
            self.restart_game(randomize_seed=False)
            return
        if action == "restart_new_board":
            self.restart_game(randomize_seed=True)
            return
        if action == "initial_roll":
            self.handle_initial_key_roll()
            return
        if action == "roll_dice":
            self.handle_roll_dice()
            return
        if action == "end_turn":
            self.finish_current_turn()
            return
        if action == "mode_road":
            self.set_action_mode("road")
            return
        if action == "mode_settlement":
            self.set_action_mode("settlement")
            return
        if action == "mode_city":
            self.set_action_mode("city")
            return
        if action == "buy_dev":
            self.buy_development_card()
            return
        if action == "bank_trade":
            self.start_bank_trade()
            return
        if action == "domestic_trade":
            self.start_domestic_trade()
            return
        if action == "domestic_trade_broadcast":
            self.select_domestic_trade_broadcast()
            return
        if action.startswith("domestic_trade_partner_"):
            player_index = int(action.rsplit("_", 1)[1])
            self.select_domestic_trade_partner(player_index)
            return
        if action == "domestic_trade_edit_give":
            self.set_domestic_trade_edit_side("give")
            return
        if action == "domestic_trade_edit_receive":
            self.set_domestic_trade_edit_side("receive")
            return
        if action.startswith("domestic_trade_adjust_"):
            _, _, _, side, resource_name, direction = action.split("_")
            delta = 1 if direction == "plus" else -1
            self.adjust_domestic_trade_resource(
                side, ResourceType[resource_name], delta
            )
            return
        if action == "domestic_trade_submit":
            self.submit_domestic_trade_offer()
            return
        if action == "domestic_trade_reveal":
            self.reveal_domestic_trade_response()
            return
        if action == "domestic_trade_accept":
            self.accept_domestic_trade()
            return
        if action == "domestic_trade_counter":
            self.begin_domestic_trade_counter()
            return
        if action == "domestic_trade_reject":
            self.reject_domestic_trade()
            return
        if action == "domestic_trade_cancel":
            self.cancel_domestic_trade()
            return
        if action == "player_handoff_reveal":
            self.reveal_player_handoff()
            return
        if action == "cancel_action":
            self.cancel_selection()
            return
        if action == "finish_road_building":
            self.complete_road_building_phase()
            return
        if action == "use_knight":
            self.use_knight_card()
            return
        if action == "use_road_building":
            self.use_road_building_card()
            return
        if action == "use_year_of_plenty":
            self.use_year_of_plenty_card()
            return
        if action == "use_monopoly":
            self.use_monopoly_card()
            return
        if action.startswith("trade_resource_"):
            resource_name = action.removeprefix("trade_resource_")
            self.select_bank_trade_resource(ResourceType[resource_name])
            return
        if action.startswith("select_resource_"):
            resource_name = action.removeprefix("select_resource_")
            resource_type = ResourceType[resource_name]
            if self.special_phase == "discard":
                self.discard_resource(resource_type)
            elif self.special_phase in ("year_of_plenty", "monopoly"):
                self.handle_resource_selection(resource_type)

    def handle_seed_input_key(self, event):
        if event.key == pygame.K_ESCAPE:
            self.seed_input_active = False
            return True
        if event.key == pygame.K_RETURN:
            self.apply_seed_text()
            return True
        if event.key == pygame.K_BACKSPACE:
            self.board_seed_text = self.board_seed_text[:-1]
            return True
        if event.unicode.isdigit() and len(self.board_seed_text) < 10:
            self.board_seed_text += event.unicode
            return True
        return False

    def find_closest_node(self, mx, my, candidates=None):
        return self.get_board_rules().find_closest_node(mx, my, candidates)

    def find_closest_edge(self, mx, my, candidates=None):
        return self.get_board_rules().find_closest_edge(mx, my, candidates)

    def get_adjacent_nodes(self, node):
        return self.get_board_rules().get_adjacent_nodes(node)

    def find_closest_tile(self, mx, my, candidates=None):
        return self.get_board_rules().find_closest_tile(mx, my, candidates)

    def road_exists_between(self, node1, node2):
        return self.get_board_rules().road_exists_between(node1, node2)

    def is_spacing_rule_satisfied(self, node):
        return self.get_board_rules().is_spacing_rule_satisfied(node)

    def player_has_road_touching_node(self, player, node):
        return self.get_board_rules().player_has_road_touching_node(player, node)

    def can_place_initial_settlement(self, node):
        allowed, message = self.get_board_rules().can_place_initial_settlement(node)
        if allowed and not self.get_public_node_tiles(node):
            return False, "未探索タイルだけに接する交差点には配置できません。"
        return allowed, message

    def can_place_main_settlement(self, player, node):
        allowed, message = self.get_board_rules().can_place_main_settlement(player, node)
        if allowed and not self.get_public_node_tiles(node):
            return False, "未探索タイルだけに接する交差点には建設できません。"
        return allowed, message

    def can_use_node_for_road_connection(self, player, node):
        return self.get_board_rules().can_use_node_for_road_connection(player, node)

    def can_place_road(self, player, node1, node2):
        if self.is_forecast_edge_blocked((node1, node2)):
            return False, "地震で通行不能な区画には街道を建設できません。"
        allowed, message = self.get_board_rules().can_place_road(player, node1, node2)
        if allowed and not self.frontier_edge_is_reachable((node1, node2)):
            return False, "街道は公開済みタイルの境界から探索を進めてください。"
        return allowed, message

    def can_upgrade_to_city(self, player, node):
        return self.get_board_rules().can_upgrade_to_city(player, node)

    def start_robber_phase(self, with_discard=True):
        self.action_mode = None
        self.robber_target_players = []
        self.resource_selection_remaining = 0
        self.free_roads_remaining = 0

        players_to_discard = []
        if with_discard and not self.house_rules.skip_discard_on_seven:
            players_to_discard = [
                player
                for player in self.turn_order
                if player.total_resource_count() > ROBBER_DISCARD_THRESHOLD
            ]
        self.discard_queue = players_to_discard
        self.discard_player = None
        self.discard_remaining = 0

        if self.discard_queue:
            self.advance_discard_phase(previous_player=self.get_current_player())
            return

        self.begin_robber_move_phase()

    def advance_discard_phase(self, previous_player=None):
        # The exact card just discarded is private to that player.  Remove its
        # transient message before another player (or the robber mover) sees the UI.
        self.feedback.clear()
        if not self.discard_queue:
            self.begin_robber_move_phase()
            active_player = self.get_current_player()
            if self.should_hide_for_handoff(previous_player, active_player):
                self.begin_player_handoff(
                    active_player,
                    return_phase="move_robber",
                    context="盗賊の移動",
                )
            return

        self.discard_player = self.discard_queue.pop(0)
        self.discard_remaining = self.discard_player.total_resource_count() // 2
        if self.discard_remaining <= 0:
            self.advance_discard_phase(previous_player=previous_player)
            return

        self.special_phase = "discard"
        if self.should_hide_for_handoff(previous_player, self.discard_player):
            self.begin_player_handoff(
                self.discard_player,
                return_phase="discard",
                context="捨て札",
            )

        self.add_log(
            f"{self.discard_player.name} は {self.discard_remaining} 枚捨ててください。"
            " 1:木 2:羊 3:麦 4:土 5:鉄"
        )

    def discard_resource(self, resource_type):
        if self.special_phase != "discard" or self.discard_player is None:
            return

        if self.discard_player.resources.get(resource_type, 0) <= 0:
            self.notify_invalid(
                f"{self.discard_player.name} は {resource_type.name} を持っていません。"
            )
            return

        self.cancel_all_trade_market_orders(
            self.discard_player,
            reason="捨て札",
        )
        removal = self.discard_player.remove_owned_resource(resource_type)
        if removal is None:
            self.notify_invalid(
                f"{self.discard_player.name} は {resource_type.name} を持っていません。"
            )
            return
        self.bank.deposit(resource_type)
        self.discard_remaining -= 1
        self.add_log(
            f"{self.discard_player.name} が資源を1枚捨てました。"
            f" 残り {self.discard_remaining} 枚"
        )
        if not self.discard_player.is_ai:
            self.notify(
                f"{RESOURCE_LABELS[resource_type]}を捨てました。残り {self.discard_remaining} 枚",
                level="info",
                log=False,
            )

        if self.discard_remaining == 0:
            completed_player = self.discard_player
            self.add_log(f"{completed_player.name} の捨て札が完了しました。")
            self.discard_player = None
            self.advance_discard_phase(previous_player=completed_player)

    def begin_robber_move_phase(self):
        self.special_phase = "move_robber"
        self.discard_player = None
        self.discard_remaining = 0
        self.robber_tile_candidates = [
            tile
            for tile in self.board.tiles
            if tile != self.board.robber_tile and self.is_frontier_tile_revealed(tile)
        ]
        self.add_log("盗賊を移動してください。現在いる地形には置けません。")

    def get_robber_target_players(self, tile):
        targets = []
        for node in tile.corners:
            if node.building is None:
                continue
            owner = node.building.owner
            if owner == self.get_current_player():
                continue
            if owner not in targets:
                targets.append(owner)
        return targets

    def relocate_robber(self, tile):
        self.board.move_robber_to(tile)
        self.play_sound("robber")
        self.add_log(f"盗賊を ({tile.x}, {tile.y}) に移動しました。")
        current_player = self.get_current_player()
        self.record_event(
            f"{current_player.name}が盗賊を移動",
            "生産を止める地形を変更",
            level="warning",
            actor=current_player,
        )
        target_players = self.get_robber_target_players(tile)

        if not target_players:
            self.complete_robber_phase()
            return

        if len(target_players) == 1:
            self.steal_random_resource(target_players[0])
            self.complete_robber_phase()
            return

        self.special_phase = "steal"
        self.robber_target_players = target_players
        target_names = ", ".join(player.name for player in target_players)
        self.add_log(f"略奪対象を選んでください: {target_names}")

    def handle_robber_move_click(self, pos):
        mx, my = pos
        tile, min_dist = self.find_closest_tile(mx, my, self.robber_tile_candidates)
        if tile is None or min_dist >= TILE_SELECTION_RADIUS:
            self.notify_invalid(
                "盗賊を移動したい地形の中央付近をクリックしてください。"
            )
            return
        self.relocate_robber(tile)

    def handle_robber_target_click(self, pos):
        mx, my = pos
        candidate_nodes = [
            node
            for node in self.board.robber_tile.corners
            if node.building is not None
            and node.building.owner in self.robber_target_players
        ]
        closest_node, min_dist = self.find_closest_node(mx, my, candidate_nodes)
        if closest_node is None or min_dist >= NODE_SELECTION_RADIUS:
            self.notify_invalid("略奪したい相手の建物をクリックしてください。")
            return

        self.steal_random_resource(closest_node.building.owner)
        self.complete_robber_phase()

    def steal_random_resource(self, victim):
        current_player = self.get_current_player()
        self.cancel_all_trade_market_orders(victim, reason="略奪")
        available_resources = [
            resource
            for resource, amount in victim.resources.items()
            for _ in range(amount)
        ]
        if not available_resources:
            self.notify(
                f"{victim.name} は資源を持っていなかったため、略奪は空振りでした。",
                level="warning",
            )
            return None

        stolen_resource = random.choice(available_resources)
        removal = victim.remove_owned_resource(stolen_resource)
        if removal is None:  # Defensive: selection was built from total ownership.
            return None
        current_player.add_resource(stolen_resource)
        self.play_sound("robber")
        self.add_log(
            f"{current_player.name} が {victim.name} から資源を1枚盗みました。"
        )
        self.record_event(
            f"{current_player.name}が略奪",
            f"{victim.name}から資源を1枚獲得",
            level="success",
            actor=current_player,
        )
        if not current_player.is_ai:
            self.notify(
                f"略奪した資源: {RESOURCE_LABELS[stolen_resource]}",
                level="success",
                log=False,
            )
        return stolen_resource

    def complete_robber_phase(self):
        self.special_phase = None
        self.discard_queue = []
        self.discard_player = None
        self.discard_remaining = 0
        self.robber_tile_candidates = []
        self.robber_target_players = []
        self.add_log("盗賊フェイズ完了。引き続き手番を続けてください。")

    def has_legal_road_placement(self, player):
        return bool(self.get_buildable_road_edges(player, require_affordability=False))

    def buy_development_card(self):
        if self.phase != "main" or self.winner is not None:
            return
        if self.special_phase is not None:
            self.notify_invalid("先に進行中の特殊処理を完了してください。")
            return
        if not self.dice_rolled:
            self.notify_invalid("発展カードの購入はダイスを振った後に行ってください。")
            return
        if not self.development_deck:
            self.notify_invalid("発展カードの山札がありません。")
            return

        current_player = self.get_current_player()
        if not current_player.can_afford(BUILD_COSTS["development"]):
            self.notify_invalid("資源不足: 発展カードには鉄・羊・麦が1枚ずつ必要です。")
            return

        self.pay_resource_cost(current_player, BUILD_COSTS["development"])
        card_type = self.development_deck.pop()
        current_player.add_development_card(card_type, available=False)
        self.play_sound("card")
        self.add_log(
            f"{current_player.name} が発展カードを1枚購入しました（残り {len(self.development_deck)} 枚）。"
        )
        self.record_event(
            f"{current_player.name}が発展カードを購入",
            "羊 -1 / 麦 -1 / 鉄 -1",
            level="success",
            actor=current_player,
        )
        if not current_player.is_ai:
            self.notify(
                f"購入した発展カード: {DEVELOPMENT_CARD_LABELS[card_type]}",
                level="success",
                log=False,
            )
        self.check_for_winner(current_player)

    def can_use_development_card(self, player, card_type):
        if self.phase != "main" or self.winner is not None:
            return False, "いまは発展カードを使えません。"
        if self.special_phase is not None:
            return False, "進行中の特殊処理が終わってから使ってください。"
        if self.development_card_used_this_turn:
            return False, "発展カードは1ターンに1枚までです。"
        if not player.has_playable_development_card(card_type):
            return False, f"{DEVELOPMENT_CARD_LABELS[card_type]} を持っていません。"
        if (
            card_type == DevelopmentCardType.YEAR_OF_PLENTY
            and self.bank.total_cards() <= 0
        ):
            return False, "銀行に受け取れる資源カードがありません。"
        return True, ""

    def use_knight_card(self):
        player = self.get_current_player()
        can_use, message = self.can_use_development_card(
            player, DevelopmentCardType.KNIGHT
        )
        if not can_use:
            self.notify_invalid(message)
            return

        player.use_development_card(DevelopmentCardType.KNIGHT)
        player.played_knights += 1
        self.development_card_used_this_turn = True
        self.play_sound("card")
        self.add_log(f"{player.name} が騎士カードを使用しました。")
        self.record_event(
            f"{player.name}が騎士を使用", "盗賊を移動します", actor=player
        )
        self.update_largest_army()
        self.check_for_winner(player)
        if self.phase != "finished":
            self.start_robber_phase(with_discard=False)

    def use_year_of_plenty_card(self):
        player = self.get_current_player()
        can_use, message = self.can_use_development_card(
            player, DevelopmentCardType.YEAR_OF_PLENTY
        )
        if not can_use:
            self.notify_invalid(message)
            return

        player.use_development_card(DevelopmentCardType.YEAR_OF_PLENTY)
        self.development_card_used_this_turn = True
        self.special_phase = "year_of_plenty"
        self.resource_selection_remaining = 2
        self.play_sound("card")
        self.add_log(
            f"{player.name} が収穫カードを使用しました。 2枚選んでください。1:木 2:羊 3:麦 4:土 5:鉄"
        )
        self.record_event(
            f"{player.name}が収穫を使用", "銀行から資源を2枚選択", actor=player
        )

    def use_monopoly_card(self):
        player = self.get_current_player()
        can_use, message = self.can_use_development_card(
            player, DevelopmentCardType.MONOPOLY
        )
        if not can_use:
            self.notify_invalid(message)
            return

        player.use_development_card(DevelopmentCardType.MONOPOLY)
        self.development_card_used_this_turn = True
        self.special_phase = "monopoly"
        self.play_sound("card")
        self.add_log(
            f"{player.name} が独占カードを使用しました。 資源を選んでください。1:木 2:羊 3:麦 4:土 5:鉄"
        )
        self.record_event(
            f"{player.name}が独占を使用", "対象にする資源を選択", actor=player
        )

    def use_road_building_card(self):
        player = self.get_current_player()
        can_use, message = self.can_use_development_card(
            player, DevelopmentCardType.ROAD_BUILDING
        )
        if not can_use:
            self.notify_invalid(message)
            return

        if player.roads_remaining <= 0:
            self.notify_invalid(
                f"{player.name} は街道コマがないため、街道建設カードの効果を使えません。"
            )
            return
        if not self.has_legal_road_placement(player):
            self.notify_invalid(
                f"{player.name} は配置可能な街道がないため、街道建設カードの効果を使えません。"
            )
            return

        player.use_development_card(DevelopmentCardType.ROAD_BUILDING)
        self.development_card_used_this_turn = True
        self.free_roads_remaining = min(2, player.roads_remaining)
        self.special_phase = "road_building"
        self.play_sound("card")
        self.add_log(
            f"{player.name} が街道建設カードを使用しました。"
            f" 無料の街道を {self.free_roads_remaining} 本配置できます。"
        )
        self.record_event(
            f"{player.name}が街道建設を使用",
            f"無料の街道を{self.free_roads_remaining}本配置",
            actor=player,
        )

    def complete_road_building_phase(self):
        if self.special_phase != "road_building":
            return False
        current_player = self.get_current_player()
        if self.free_roads_remaining > 0 and self.has_legal_road_placement(
            current_player
        ):
            self.notify_invalid(
                "街道建設カードは、配置可能なら残りの街道も続けて置いてください。"
            )
            return False
        self.special_phase = None
        self.free_roads_remaining = 0
        self.add_log("街道建設カードの処理が完了しました。")
        self.check_for_winner(current_player)
        return True

    def handle_resource_selection(self, resource_type):
        player = self.get_current_player()
        if self.special_phase == "year_of_plenty":
            if not self.give_resource_from_bank(player, resource_type):
                self.notify_invalid(
                    f"銀行に {RESOURCE_LABELS[resource_type]} が残っていません。"
                )
                return
            self.record_public_gain(player, {resource_type: 1}, "収穫")
            self.resource_selection_remaining -= 1
            self.add_log(
                f"{player.name} が {resource_type.name} を獲得しました。"
                f" 残り {self.resource_selection_remaining} 枚選択"
            )
            self.record_event(
                f"{player.name}が収穫で獲得",
                f"{RESOURCE_LABELS[resource_type]} +1 / 残り{self.resource_selection_remaining}枚",
                level="success",
                actor=player,
            )
            if self.resource_selection_remaining == 0:
                self.special_phase = None
                self.add_log("収穫カードの処理が完了しました。")
            else:
                self.complete_resource_selection_if_bank_empty()
            return

        if self.special_phase == "monopoly":
            total_taken = 0
            for other_player in self.players:
                if other_player == player:
                    continue
                self.cancel_all_trade_market_orders(
                    other_player,
                    reason="独占",
                )
                amount = other_player.resources.get(resource_type, 0)
                if amount <= 0:
                    continue
                removal = other_player.remove_owned_resource(resource_type, amount)
                if removal is None:  # Defensive: amount came from total ownership.
                    continue
                player.add_resource(resource_type, amount)
                total_taken += amount
            self.special_phase = None
            if total_taken > 0:
                self.record_public_gain(player, {resource_type: total_taken}, "独占")
            self.add_log(
                f"{player.name} が独占カードで {resource_type.name} を {total_taken} 枚獲得しました。"
            )
            self.record_event(
                f"{player.name}が独占で獲得",
                f"{RESOURCE_LABELS[resource_type]} +{total_taken}",
                level="success",
                actor=player,
            )

    def complete_resource_selection_if_bank_empty(self):
        if self.special_phase != "year_of_plenty" or self.bank.total_cards() > 0:
            return False
        self.resource_selection_remaining = 0
        self.special_phase = None
        self.add_log("銀行の資源カードが尽きたため、収穫カードの処理を終了しました。")
        return True

    def handle_free_road_build_click(self, pos):
        current_player = self.get_current_player()
        if self.free_roads_remaining <= 0:
            self.complete_road_building_phase()
            return

        mx, my = pos
        closest_edge, min_dist = self.find_closest_edge(mx, my)
        if closest_edge is None or min_dist >= EDGE_SELECTION_RADIUS:
            self.notify_invalid(
                "無料の街道を置きたい辺の中央付近をクリックしてください。"
            )
            return

        node1, node2 = closest_edge
        can_place, message = self.can_place_road(current_player, node1, node2)
        if not can_place:
            self.notify_invalid(message)
            return

        current_player.roads_remaining -= 1
        new_road = Road(current_player, node1, node2)
        self.board.roads.append(new_road)
        self.match_metrics.record_build(
            self.get_match_metric_player_id(current_player), "road"
        )
        self.free_roads_remaining -= 1
        self.play_sound("road")
        self.add_log(
            f"{current_player.name} が無料の街道を配置しました。"
            f" 残り {self.free_roads_remaining} 本"
        )
        self.record_event(
            f"{current_player.name}が無料の街道を建設",
            f"残り{self.free_roads_remaining}本",
            level="success",
            actor=current_player,
        )
        self.reveal_frontier_from_road(new_road)
        self.update_longest_road()
        self.check_for_winner(current_player)
        if self.phase == "finished":
            return

        if self.free_roads_remaining <= 0 or not self.has_legal_road_placement(
            current_player
        ):
            self.complete_road_building_phase()

    def update_largest_army(self):
        previous_owner = self.largest_army_owner
        max_knights = max((player.played_knights for player in self.players), default=0)

        if max_knights < MIN_LARGEST_ARMY_SIZE:
            self.largest_army_owner = None
            self.largest_army_size = 0
            return

        candidates = [
            player for player in self.players if player.played_knights == max_knights
        ]
        if self.largest_army_owner in candidates:
            self.largest_army_size = max_knights
            return

        if len(candidates) == 1:
            self.largest_army_owner = candidates[0]
            self.largest_army_size = max_knights
            if previous_owner != candidates[0]:
                self.add_log(
                    f"最大騎士力: {candidates[0].name} が獲得 ({max_knights} 枚)"
                )
                self.record_event(
                    f"{candidates[0].name}が最大騎士力を獲得",
                    f"騎士{max_knights}枚使用・2 VP",
                    level="success",
                    actor=candidates[0],
                )
            return

        self.largest_army_owner = None
        self.largest_army_size = max_knights

    def grant_initial_resources(self, player, settlement_node):
        gained_resources = {}
        for tile in self.get_public_node_tiles(settlement_node):
            if tile.resource_type == ResourceType.DESERT:
                continue
            if self.give_resource_from_bank(player, tile.resource_type):
                gained_resources[tile.resource_type] = (
                    gained_resources.get(tile.resource_type, 0) + 1
                )

        if gained_resources:
            gain_text = self.format_resource_bundle(gained_resources)
            self.add_log(f"{player.name} は初期資源を獲得: {gain_text}")
            self.record_public_gain(player, gained_resources, "初期配置")
        else:
            self.add_log(f"{player.name} の2回目の開拓地は砂漠に隣接しています。")

    def handle_initial_key_roll(self):
        if (
            not self.initial_dice_phase
            or not self.players
            or self.has_active_dice_animation()
        ):
            return
        current_player = self.initial_dice_contenders[self.initial_player_index]
        dice_values = roll_two_dice()
        self.start_dice_animation(
            "initial", dice_values, current_player.name, "初期ダイス"
        )

    def resolve_initial_key_roll(self, dice_roll):
        current_player = self.initial_dice_contenders[self.initial_player_index]
        self.initial_dice_results[current_player.name] = dice_roll
        self.initial_dice_histories[current_player.name].append(dice_roll)
        self.add_log(f"{current_player.name} の初期ダイスの目: {dice_roll}")
        self.record_event(
            "初期ダイス",
            f"{current_player.name}: {dice_roll}",
            actor=current_player,
            include_in_turn=False,
        )
        self.initial_player_index += 1

        if self.initial_player_index < len(self.initial_dice_contenders):
            return

        self.resolve_initial_dice_round()

    def resolve_initial_dice_round(self):
        highest_score = max(self.initial_dice_results.values())
        highest_players = [
            player
            for player in self.initial_dice_contenders
            if self.initial_dice_results[player.name] == highest_score
        ]

        if len(highest_players) > 1:
            tied_names = ", ".join(player.name for player in highest_players)
            self.add_log(
                f"最高点同点: {tied_names} が {highest_score} で並びました。再ロールします。"
            )
            self.initial_dice_contenders = highest_players
            self.initial_dice_results = {}
            self.initial_player_index = 0
            self.add_log(
                f"次は {self.initial_dice_contenders[0].name} の再ロールです。"
            )
            return

        self.finalize_initial_dice_order(highest_players[0])

    def finalize_initial_dice_order(self, starting_player):
        starting_index = self.players.index(starting_player)
        self.turn_order = self.players[starting_index:] + self.players[:starting_index]
        self.initial_placement_order = self.turn_order.copy()
        self.add_log("初期配置順（第1ラウンド）:")
        for index, player in enumerate(self.initial_placement_order, start=1):
            self.add_log(
                f"{index}: {player.name} (ダイス: {self.get_initial_dice_history_text(player)})"
            )
        self.initial_dice_phase = False
        self.initial_player_index = 0
        self.initial_dice_contenders = []
        self.initial_dice_pending_groups = []
        self.add_log("初期ダイスが完了しました。")
        self.add_log("光っている候補をクリックして、開拓地と街道を配置してください。")
        self.record_event(
            "初期配置順が決定",
            " → ".join(player.name for player in self.initial_placement_order),
            actor=starting_player,
            include_in_turn=False,
        )

    def handle_roll_dice(self):
        if (
            self.phase != "main"
            or self.winner is not None
            or self.has_active_dice_animation()
        ):
            return
        if self.special_phase is not None:
            self.notify_invalid("進行中の特殊処理を先に完了してください。")
            return
        if self.dice_rolled:
            self.notify_invalid("このターンはすでにダイスを振っています。")
            return
        self.feedback.clear()
        dice_values = roll_two_dice()
        current_player = self.get_current_player()
        player_name = current_player.name if current_player is not None else ""
        self.start_dice_animation("main", dice_values, player_name, "ダイスロール")

    def resolve_main_dice_roll(self, dice_roll):
        current_player = self.get_current_player()
        player_name = (
            current_player.name if current_player is not None else "プレイヤー"
        )
        self.add_log(f"{player_name} のダイスの目: {dice_roll}")
        self.record_dice_luck(dice_roll)
        if dice_roll == 7:
            robber_detail = (
                "捨て札なしで盗賊を移動します"
                if self.house_rules.skip_discard_on_seven
                else "捨て札と盗賊の移動を処理します"
            )
            self.record_event(
                f"{current_player.name}のダイス: 7",
                robber_detail,
                level="warning",
                actor=current_player,
            )
            if current_player is not None and current_player.is_ai:
                self.set_ai_status(
                    current_player,
                    "出目 7 を解決",
                    robber_detail,
                )
            self.start_robber_phase()
        else:
            gains = self.distribute_resources(dice_roll)
            gain_text = " / ".join(gains) if gains else "資源の獲得はありません"
            self.record_event(
                f"{current_player.name}のダイス: {dice_roll}",
                gain_text,
                level="success" if gains else "info",
                actor=current_player,
            )
            self.add_log("建設・交易・交渉を行うか、手番を終了してください。")
            if current_player is not None and current_player.is_ai:
                public_gain = self.get_recent_public_gain_text(current_player)
                detail = (
                    f"公開獲得: {public_gain}"
                    if current_player.name in self.last_resource_distribution
                    else "この出目での獲得はありません"
                )
                self.set_ai_status(current_player, f"出目 {dice_roll} を解決", detail)
        self.dice_rolled = True
        if current_player is not None and current_player.is_ai:
            self.schedule_ai_action(1.6)

    def record_dice_luck(self, dice_roll):
        """Compare this roll's production with each player's statistical mean.

        Bank shortages are deliberately excluded: the index measures dice luck,
        not whether the shared bank happened to run out of a resource.
        """

        for player in self.players:
            expected_units = 0.0
            actual_units = 0
            for node in self.board.nodes:
                building = node.building
                if building is None or building.owner is not player:
                    continue
                multiplier = building.resource_multiplier
                for tile in node.tiles:
                    if tile is self.board.robber_tile:
                        continue
                    expected_units += (
                        get_token_pip_count(tile.number) * multiplier / 36.0
                    )
                    if tile.number == dice_roll:
                        actual_units += multiplier
            self.match_metrics.record_production(
                self.get_match_metric_player_id(player),
                actual_units=actual_units,
                expected_units=expected_units,
            )

    def advance_initial_phase(self, current_player):
        self.add_log(f"{current_player.name} の初期配置が完了しました。")

        if all(count >= 2 for count in self.initial_placement_counts.values()):
            self.start_main_phase(previous_player=current_player)
            return

        if self.initial_round == 1 and all(
            count >= 1 for count in self.initial_placement_counts.values()
        ):
            self.initial_round = 2
            self.initial_placement_order = list(reversed(self.turn_order))
            self.initial_player_index = 0
            self.add_log("初期配置フェーズ 第2ラウンド開始（逆順）")
            self.add_log(f"次は {self.initial_placement_order[0].name} の配置です。")
            self.add_log("2回目の開拓地では隣接するタイルの資源を獲得します。")
            self.schedule_ai_action()
            return

        self.initial_player_index += 1
        next_player = self.initial_placement_order[self.initial_player_index]
        self.add_log(f"次は {next_player.name} の配置です。")
        if self.should_hide_for_handoff(current_player, next_player):
            self.begin_player_handoff(next_player, context="初期配置")
        self.schedule_ai_action()

    def start_main_phase(self, previous_player=None):
        self.phase = "main"
        self.current_player_index = 0
        self.waiting_for_road = False
        self.last_settlement_node = None
        self.show_help_panel = False
        self.seed_input_active = False
        self.feedback.clear()
        self.reset_turn_state()
        self.reset_pending_dice_state()
        first_player = self.get_current_player()
        self.turn_summary_entries = []
        self.add_log("初期配置フェーズ完了。通常フェーズを開始します。")
        self.add_log(f"最初の手番: {first_player.name}")
        self.add_log(
            "スペースでダイス、発展カードは K/B/Y/M、銀行交易は T、交渉は P です。"
        )
        self.record_event(
            "通常ゲームを開始",
            f"最初の手番は{first_player.name}",
            actor=first_player,
            include_in_turn=False,
        )
        self.announce_initial_forecast_event()
        if self.should_hide_for_handoff(previous_player, first_player):
            self.begin_player_handoff(first_player, context="最初の手番")
        if first_player is not None and first_player.is_ai:
            self.set_ai_status(
                first_player,
                "手番を開始",
                f"公開手札総数 {first_player.total_resource_count()}枚",
            )
        self.schedule_ai_action()

    def handle_initial_placement(self, pos):
        mx, my = pos
        current_player = self.initial_placement_order[self.initial_player_index]

        if not self.waiting_for_road:
            closest_node, min_dist = self.find_closest_node(mx, my)
            if not closest_node or min_dist >= NODE_SELECTION_RADIUS:
                self.notify_invalid("有効なノードが見つかりませんでした。")
                return

            can_place, message = self.can_place_initial_settlement(closest_node)
            if not can_place:
                self.notify_invalid(message)
                return
            if current_player.settlements_remaining <= 0:
                self.notify_invalid("開拓地コマが残っていません。")
                return

            current_player.settlements_remaining -= 1
            closest_node.building = Building(current_player)
            self.match_metrics.record_build(
                self.get_match_metric_player_id(current_player), "settlement"
            )
            self.play_sound("build")
            self.add_log(
                f"{current_player.name} が ({closest_node.x:.1f}, {closest_node.y:.1f}) に"
                f"開拓地を配置 (Round {self.initial_round})"
            )
            self.record_event(
                f"{current_player.name}が開拓地を配置",
                f"初期配置 {self.initial_round}周目",
                level="success",
                actor=current_player,
                include_in_turn=False,
            )
            if self.initial_placement_counts[current_player.name] == 1:
                self.grant_initial_resources(current_player, closest_node)

            self.last_settlement_node = closest_node
            self.waiting_for_road = True
            self.add_log("続けて隣接する辺に街道を配置してください。")
            return

        adjacent_nodes = self.get_adjacent_nodes(self.last_settlement_node)
        candidate_node, min_dist = self.find_closest_node(mx, my, adjacent_nodes)
        if not candidate_node or min_dist >= NODE_SELECTION_RADIUS:
            self.notify_invalid("有効な隣接ノードが選択されませんでした。")
            return
        if self.road_exists_between(self.last_settlement_node, candidate_node):
            self.notify_invalid("その辺には既に街道があります。")
            return
        if not self.frontier_edge_is_reachable(
            (self.last_settlement_node, candidate_node)
        ):
            self.notify_invalid("公開済みタイルの境界から探索を進めてください。")
            return
        if current_player.roads_remaining <= 0:
            self.notify_invalid("街道コマが残っていません。")
            return

        current_player.roads_remaining -= 1
        new_road = Road(current_player, self.last_settlement_node, candidate_node)
        self.board.roads.append(new_road)
        self.match_metrics.record_build(
            self.get_match_metric_player_id(current_player), "road"
        )
        self.play_sound("road")
        self.add_log(
            f"{current_player.name} が ({self.last_settlement_node.x:.1f}, {self.last_settlement_node.y:.1f}) から"
            f" ({candidate_node.x:.1f}, {candidate_node.y:.1f}) に街道を配置 (Round {self.initial_round})"
        )
        self.record_event(
            f"{current_player.name}が街道を配置",
            f"初期配置 {self.initial_round}周目を完了",
            level="success",
            actor=current_player,
            include_in_turn=False,
        )
        self.reveal_frontier_from_road(new_road)
        self.initial_placement_counts[current_player.name] += 1
        self.waiting_for_road = False
        self.last_settlement_node = None
        self.update_longest_road()
        self.advance_initial_phase(current_player)

    def set_action_mode(self, action_mode):
        if self.phase != "main" or self.winner is not None:
            return
        if self.special_phase is not None:
            self.notify_invalid("進行中の特殊処理を完了してください。")
            return
        if not self.dice_rolled:
            self.notify_invalid("先にスペースキーでダイスを振ってください。")
            return
        player = self.get_current_player()
        if player is None:
            return

        guidance = []
        if action_mode == "road":
            road_cost, _waived = self.get_effective_road_cost(player)
            guidance = build_action_mode_guidance(
                "road",
                self.get_build_preview(
                    "街道", player, road_cost, player.roads_remaining > 0
                ),
                len(self.get_buildable_road_edges(player)),
            )
        elif action_mode == "settlement":
            guidance = build_action_mode_guidance(
                "settlement",
                self.get_build_preview(
                    "開拓地",
                    player,
                    BUILD_COSTS["settlement"],
                    player.settlements_remaining > 0,
                ),
                len(self.get_buildable_settlement_nodes(player)),
            )
        elif action_mode == "city":
            guidance = build_action_mode_guidance(
                "city",
                self.get_build_preview(
                    "都市", player, BUILD_COSTS["city"], player.cities_remaining > 0
                ),
                len(self.get_buildable_city_nodes(player)),
            )

        if guidance and "不可" in guidance[0]:
            self.notify_invalid(guidance[1])
            return

        self.feedback.clear()
        self.action_mode = action_mode
        action_messages = {
            "road": "街道モード: 六角形の辺の中央付近をクリックしてください。",
            "settlement": "開拓地モード: 建設したい交差点をクリックしてください。",
            "city": "都市モード: 自分の開拓地をクリックしてください。",
        }
        self.add_log(action_messages[action_mode])

    def finish_current_turn(self):
        if self.winner is not None:
            return
        if self.special_phase is not None:
            self.notify_invalid("盗賊の処理が終わるまで手番を終了できません。")
            return
        if not self.dice_rolled:
            self.notify_invalid("まだダイスを振っていません。")
            return

        current_player = self.get_current_player()
        summary = (
            " / ".join(self.turn_summary_entries[-2:])
            if self.turn_summary_entries
            else "追加の行動なし"
        )
        self.record_event(
            f"{current_player.name}の手番終了",
            summary,
            actor=current_player,
            include_in_turn=False,
        )
        current_player.activate_new_development_cards()
        self.feedback.clear()
        self.reset_turn_state()
        self.current_player_index = (self.current_player_index + 1) % len(
            self.turn_order
        )
        self.schedule_ai_action()
        self.turn_summary_entries = []
        next_player = self.get_current_player()
        # Forecast transitions belong to the boundary before the next turn.
        # Apply them before checking that player's score: an earthquake can
        # suspend or restore Longest Road and therefore change the public VP
        # total at exactly this boundary.
        self.advance_variant_turn_boundary()
        self.check_for_winner(next_player)
        if self.phase == "finished":
            return
        self.add_log(f"{next_player.name} の手番です。")
        self.add_log(
            "スペースでダイス、発展カードは K/B/Y/M、銀行交易は T、交渉は P です。"
        )
        if next_player.is_ai:
            self.set_ai_status(
                next_player,
                "手番を開始",
                f"公開手札総数 {next_player.total_resource_count()}枚",
            )
        if self.should_hide_for_handoff(current_player, next_player):
            self.begin_player_handoff(next_player, context="手番")

    def build_settlement(self, pos):
        current_player = self.get_current_player()
        if current_player.settlements_remaining <= 0:
            self.notify_invalid("開拓地コマが残っていません。")
            return
        if not current_player.can_afford(BUILD_COSTS["settlement"]):
            self.notify_invalid("資源不足: 開拓地には木・土・羊・麦が1枚ずつ必要です。")
            return

        mx, my = pos
        closest_node, min_dist = self.find_closest_node(mx, my)
        if not closest_node or min_dist >= NODE_SELECTION_RADIUS:
            self.notify_invalid("有効なノードが見つかりませんでした。")
            return

        can_place, message = self.can_place_main_settlement(
            current_player, closest_node
        )
        if not can_place:
            self.notify_invalid(message)
            return

        self.pay_resource_cost(current_player, BUILD_COSTS["settlement"])
        current_player.settlements_remaining -= 1
        closest_node.building = Building(current_player)
        self.match_metrics.record_build(
            self.get_match_metric_player_id(current_player), "settlement"
        )
        self.action_mode = None
        self.feedback.clear()
        self.play_sound("build")
        self.add_log(f"{current_player.name} が開拓地を建設しました。")
        self.record_event(
            f"{current_player.name}が開拓地を建設",
            "木 -1 / 土 -1 / 羊 -1 / 麦 -1",
            level="success",
            actor=current_player,
        )
        self.update_longest_road()
        self.check_for_winner(current_player)

    def build_city(self, pos):
        current_player = self.get_current_player()
        if current_player.cities_remaining <= 0:
            self.notify_invalid("都市コマが残っていません。")
            return
        if not current_player.can_afford(BUILD_COSTS["city"]):
            self.notify_invalid("資源不足: 都市には鉄3枚と麦2枚が必要です。")
            return

        mx, my = pos
        closest_node, min_dist = self.find_closest_node(mx, my)
        if not closest_node or min_dist >= NODE_SELECTION_RADIUS:
            self.notify_invalid("有効なノードが見つかりませんでした。")
            return

        can_upgrade, message = self.can_upgrade_to_city(current_player, closest_node)
        if not can_upgrade:
            self.notify_invalid(message)
            return

        self.pay_resource_cost(current_player, BUILD_COSTS["city"])
        current_player.cities_remaining -= 1
        current_player.settlements_remaining += 1
        closest_node.building.upgrade_to_city()
        self.match_metrics.record_build(
            self.get_match_metric_player_id(current_player), "city"
        )
        self.action_mode = None
        self.feedback.clear()
        self.play_sound("build")
        self.add_log(f"{current_player.name} が都市にアップグレードしました。")
        self.record_event(
            f"{current_player.name}が都市へ発展",
            "麦 -2 / 鉄 -3 / VP +1",
            level="success",
            actor=current_player,
        )
        self.check_for_winner(current_player)

    def build_road(self, pos):
        current_player = self.get_current_player()
        if current_player.roads_remaining <= 0:
            self.notify_invalid("街道コマが残っていません。")
            return
        road_cost, waived_resource = self.get_effective_road_cost(current_player)
        if not current_player.can_afford(road_cost):
            requirement = (
                "木1枚または土1枚が必要です。"
                if waived_resource is not None
                else "木1枚と土1枚が必要です。"
            )
            self.notify_invalid(f"資源不足: 街道には{requirement}")
            return

        mx, my = pos
        closest_edge, min_dist = self.find_closest_edge(mx, my)
        if closest_edge is None or min_dist >= EDGE_SELECTION_RADIUS:
            self.notify_invalid("街道を置きたい辺の中央付近をクリックしてください。")
            return

        node1, node2 = closest_edge
        can_place, message = self.can_place_road(current_player, node1, node2)
        if not can_place:
            self.notify_invalid(message)
            return

        self.pay_resource_cost(current_player, road_cost)
        current_player.roads_remaining -= 1
        new_road = Road(current_player, node1, node2)
        self.board.roads.append(new_road)
        self.match_metrics.record_build(
            self.get_match_metric_player_id(current_player), "road"
        )
        self.action_mode = None
        self.feedback.clear()
        self.play_sound("road")
        discount_text = ""
        if waived_resource is not None and self.consume_construction_boom():
            discount_text = f"（建設ブーム: {RESOURCE_LABELS[waived_resource]}免除）"
            self.add_log(
                f"建設ブーム適用 — {current_player.name} は"
                f" {RESOURCE_LABELS[waived_resource]}を支払わずに建設しました。"
            )
        self.add_log(f"{current_player.name} が街道を建設しました。{discount_text}")
        paid_text = " / ".join(
            f"{RESOURCE_LABELS[resource_type]} -{amount}"
            for resource_type, amount in road_cost.items()
            if amount > 0
        )
        self.record_event(
            f"{current_player.name}が街道を建設",
            f"{paid_text}{' / 建設ブーム適用' if discount_text else ''}",
            level="success",
            actor=current_player,
        )
        self.reveal_frontier_from_road(new_road)
        self.update_longest_road()
        self.check_for_winner(current_player)

    def handle_main_phase_click(self, pos):
        if self.special_phase == "move_robber":
            self.handle_robber_move_click(pos)
            return
        if self.special_phase == "steal":
            self.handle_robber_target_click(pos)
            return
        if self.special_phase == "road_building":
            self.handle_free_road_build_click(pos)
            return
        if self.special_phase is not None:
            self.notify_invalid("先に盗賊の処理を完了してください。")
            return
        if not self.dice_rolled:
            self.notify_invalid("先にダイスを振ってください。")
            return
        if self.action_mode is None:
            self.notify_invalid(
                "行動未選択: 右のボタンから行動を選ぶか、Enter で手番終了してください。"
            )
            return
        if self.action_mode == "settlement":
            self.build_settlement(pos)
        elif self.action_mode == "city":
            self.build_city(pos)
        elif self.action_mode == "road":
            self.build_road(pos)

    def get_player_longest_road_length(self, player):
        player_roads = [
            road
            for road in self.board.roads
            if road.owner == player and self.is_road_usable(road)
        ]
        if not player_roads:
            return 0

        adjacency = {}
        for road in player_roads:
            adjacency.setdefault(road.node1, []).append(road)
            adjacency.setdefault(road.node2, []).append(road)

        def dfs(node, used_road_ids):
            if node.building is not None and node.building.owner != player:
                return 0

            best = 0
            for road in adjacency.get(node, []):
                road_id = id(road)
                if road_id in used_road_ids:
                    continue
                next_node = road.other_node(node)
                if next_node is None:
                    continue
                best = max(best, 1 + dfs(next_node, used_road_ids | {road_id}))
            return best

        best_length = 0
        for road in player_roads:
            road_id = id(road)
            best_length = max(best_length, 1 + dfs(road.node1, {road_id}))
            best_length = max(best_length, 1 + dfs(road.node2, {road_id}))
        return best_length

    def update_longest_road(self):
        previous_owner = self.longest_road_owner
        lengths = {
            player: self.get_player_longest_road_length(player)
            for player in self.players
        }
        max_length = max(lengths.values(), default=0)

        if max_length < MIN_LONGEST_ROAD_LENGTH:
            self.longest_road_owner = None
            self.longest_road_length = 0
            return

        candidates = [
            player for player, length in lengths.items() if length == max_length
        ]
        if self.longest_road_owner in candidates:
            self.longest_road_length = max_length
            return

        if len(candidates) == 1:
            self.longest_road_owner = candidates[0]
            self.longest_road_length = max_length
            if previous_owner != candidates[0]:
                self.add_log(
                    f"最長交易路: {candidates[0].name} が獲得 ({max_length} 本)"
                )
                self.record_event(
                    f"{candidates[0].name}が最長交易路を獲得",
                    f"連続{max_length}本・2 VP",
                    level="success",
                    actor=candidates[0],
                )
            return

        self.longest_road_owner = None
        self.longest_road_length = max_length

    def check_for_winner(self, player):
        if self.phase != "main":
            return
        if self.replay_recorder is None and not self.replay_mode:
            self.record_match_progress(
                self.latest_event.get("title", "得点更新"),
                None,
            )
        points = self.get_player_victory_points(player)
        if points >= self.victory_point_target:
            self.winner = player
            self.phase = "finished"
            self.action_mode = None
            self.special_phase = None
            self.play_sound("victory")
            self.add_log(f"{player.name} が {points} 点に到達し、勝利しました。")
            self.add_log("右のボタンから同じ盤面または新しい盤面で再戦できます。")
            self.record_event(
                f"{player.name}の勝利",
                f"{points} VPに到達しました",
                level="success",
                actor=player,
                include_in_turn=False,
            )
            self.flush_replay_capture(force_latest=True)
            self.save_completed_replay()
            self.refresh_match_result()

    def is_ai_input_locked(self):
        if self.special_phase == "player_handoff":
            return False
        if self.phase == "initial":
            if self.initial_dice_phase:
                if not self.initial_dice_contenders:
                    return False
                return self.initial_dice_contenders[self.initial_player_index].is_ai
            if not self.initial_placement_order:
                return False
            return self.initial_placement_order[self.initial_player_index].is_ai
        if self.phase != "main":
            return False
        if self.is_domestic_trade_phase():
            actor = self.get_domestic_trade_actor()
            return actor is not None and actor.is_ai
        if self.special_phase == "discard" and self.discard_player is not None:
            return self.discard_player.is_ai
        player = self.get_current_player()
        return player is not None and player.is_ai

    def update_ai(self):
        if self.replay_mode or self.ai_paused:
            return
        now = pygame.time.get_ticks()
        if now < self.ai_next_action_at:
            return
        if self.ai.step(self):
            self.ai_next_action_at = now + self.ai_action_delay_ms

    def handle_events(self):
        self.buttons = (
            []
            if self.is_pre_game_settings_open() or self.is_lan_overlay_open()
            else self.build_buttons()
        )
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                break

            if self.is_pre_game_settings_open():
                self.handle_pre_game_settings_event(event)
                continue

            if self.is_lan_overlay_open():
                self.handle_lan_event(event)
                continue

            if self.replay_mode:
                self.handle_replay_event(event)
                continue

            if self.handle_global_ui_event(event):
                continue

            if self.phase == "finished":
                if self.show_log_panel or self.show_help_panel:
                    continue
                if event.type == pygame.MOUSEWHEEL:
                    self.move_result_selection(-event.y)
                elif event.type == pygame.KEYDOWN and event.key in (
                    pygame.K_UP,
                    pygame.K_LEFT,
                ):
                    self.move_result_selection(-1)
                elif event.type == pygame.KEYDOWN and event.key in (
                    pygame.K_DOWN,
                    pygame.K_RIGHT,
                ):
                    self.move_result_selection(1)
                elif event.type == pygame.KEYDOWN and event.key in (
                    pygame.K_RETURN,
                    pygame.K_SPACE,
                ):
                    self.handle_match_result_action(REPLAY_SELECTED_ACTION)
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_r:
                    self.handle_match_result_action(RESTART_SAME_BOARD_ACTION)
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_n:
                    self.handle_match_result_action(NEW_BOARD_ACTION)
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if self.match_result is None:
                        self.refresh_match_result()
                    if self.result_display_layout is None:
                        self.result_display_layout = self.build_match_result_layout()
                    if self.result_display_layout is not None:
                        target = hit_test_result_display(
                            self.result_display_layout,
                            event.pos,
                        )
                        if target is not None and target.kind == "event":
                            self.result_selected_event_index = target.event_index
                            self.result_display_layout = (
                                self.build_match_result_layout()
                            )
                        elif target is not None and target.kind == "action":
                            self.handle_match_result_action(target.action)
                continue

            if self.has_active_dice_animation():
                continue

            if self.is_ai_input_locked():
                continue

            if self.special_phase == "player_handoff":
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    clicked_button = self.find_clicked_button(event.pos)
                    if clicked_button is not None:
                        self.handle_button_action(clicked_button.action)
                continue

            if self.phase == "initial":
                if self.initial_dice_phase:
                    if event.type == pygame.KEYDOWN and self.seed_input_active:
                        self.handle_seed_input_key(event)
                        continue
                    if (
                        event.type == pygame.KEYDOWN
                        and event.key in (pygame.K_2, pygame.K_3, pygame.K_4)
                        and self.can_edit_pre_game_settings()
                    ):
                        self.configure_players(int(event.unicode))
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                        self.handle_initial_key_roll()
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        clicked_button = self.find_clicked_button(event.pos)
                        if clicked_button is not None:
                            if clicked_button.action != "seed_input_focus":
                                self.seed_input_active = False
                            self.handle_button_action(clicked_button.action)
                        else:
                            self.seed_input_active = False
                else:
                    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        self.handle_initial_placement(event.pos)
                continue

            if self.is_domestic_trade_phase():
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    self.cancel_domestic_trade()
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    clicked_button = self.find_clicked_button(event.pos)
                    if clicked_button is not None:
                        self.handle_button_action(clicked_button.action)
                continue

            if self.special_phase == "discard":
                if event.type == pygame.KEYDOWN:
                    resource_type = self.get_discard_key_map().get(event.key)
                    if resource_type is not None:
                        self.discard_resource(resource_type)
                    else:
                        self.notify_invalid(
                            "捨て札は 1:木 2:羊 3:麦 4:土 5:鉄 で選んでください。"
                        )
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    clicked_button = self.find_clicked_button(event.pos)
                    if clicked_button is not None:
                        self.handle_button_action(clicked_button.action)
                continue

            if self.special_phase in ("year_of_plenty", "monopoly"):
                if event.type == pygame.KEYDOWN:
                    resource_type = self.get_discard_key_map().get(event.key)
                    if resource_type is not None:
                        self.handle_resource_selection(resource_type)
                    else:
                        self.notify_invalid(
                            "資源選択は 1:木 2:羊 3:麦 4:土 5:鉄 で指定してください。"
                        )
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    clicked_button = self.find_clicked_button(event.pos)
                    if clicked_button is not None:
                        self.handle_button_action(clicked_button.action)
                continue

            if self.special_phase in ("bank_trade_give", "bank_trade_receive"):
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.cancel_selection()
                        continue
                    resource_type = self.get_discard_key_map().get(event.key)
                    if resource_type is not None:
                        self.select_bank_trade_resource(resource_type)
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    clicked_button = self.find_clicked_button(event.pos)
                    if clicked_button is not None:
                        self.handle_button_action(clicked_button.action)
                continue

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE:
                    self.handle_roll_dice()
                elif event.key == pygame.K_d:
                    self.buy_development_card()
                elif event.key == pygame.K_t:
                    self.start_bank_trade()
                elif event.key == pygame.K_p:
                    self.start_domestic_trade()
                elif event.key == pygame.K_k:
                    self.use_knight_card()
                elif event.key == pygame.K_b:
                    self.use_road_building_card()
                elif event.key == pygame.K_y:
                    self.use_year_of_plenty_card()
                elif event.key == pygame.K_m:
                    self.use_monopoly_card()
                elif event.key == pygame.K_r:
                    self.set_action_mode("road")
                elif event.key == pygame.K_s:
                    self.set_action_mode("settlement")
                elif event.key == pygame.K_c:
                    self.set_action_mode("city")
                elif event.key == pygame.K_RETURN:
                    self.finish_current_turn()
                elif event.key == pygame.K_ESCAPE:
                    if self.special_phase == "road_building":
                        self.complete_road_building_phase()
                        continue
                    self.cancel_selection()
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                clicked_button = self.find_clicked_button(event.pos)
                if clicked_button is not None:
                    self.handle_button_action(clicked_button.action)
                    continue
                self.handle_main_phase_click(event.pos)

    def distribute_resources(self, dice_roll):
        gain_summaries = []
        gains_by_player = {}
        self.last_resource_distribution = {}
        demands = {resource_type: {} for resource_type in RESOURCE_TYPES}
        tiles = [
            tile
            for tile in self.board.get_tiles_with_number(dice_roll)
            if self.is_frontier_tile_revealed(tile)
        ]
        for tile in tiles:
            if tile == self.board.robber_tile:
                self.add_log(
                    f"盗賊がいるタイル({tile.resource_type.name})は資源を生産しません。"
                )
                continue
            for node in tile.corners:
                if node.building is not None:
                    owner = node.building.owner
                    current = demands[tile.resource_type].get(owner, 0)
                    demands[tile.resource_type][owner] = (
                        current + node.building.resource_multiplier
                    )

        for resource_type, player_demands in demands.items():
            if not player_demands:
                continue
            if (
                resource_type is ResourceType.SHEEP
                and self.is_forecast_event_active(SHEEP_DROUGHT_EVENT_ID)
            ):
                self.add_log(
                    "大干ばつのため、この出目では羊タイルが生産しません。"
                )
                continue
            total_demand = sum(player_demands.values())
            available = self.bank.available(resource_type)

            if total_demand <= available:
                grants = dict(player_demands)
            elif len(player_demands) == 1:
                player, requested = next(iter(player_demands.items()))
                grants = {player: min(requested, available)}
                self.add_log(
                    f"銀行の {RESOURCE_LABELS[resource_type]} が不足し、"
                    f"{player.name} は残り {grants[player]} 枚だけ獲得します。"
                )
            else:
                self.add_log(
                    f"銀行の {RESOURCE_LABELS[resource_type]} が不足しているため、"
                    "この資源は誰にも配布されません。"
                )
                continue

            granted_players = []
            for player, amount in grants.items():
                if amount <= 0:
                    continue
                amount = self.give_resource_from_bank(
                    player,
                    resource_type,
                    amount,
                )
                if amount <= 0:
                    continue
                granted_players.append(player)
                self.add_log(
                    f"{player.name} が {RESOURCE_LABELS[resource_type]} を {amount} 枚獲得しました。"
                )
                gain_summaries.append(
                    f"{player.name}: {RESOURCE_LABELS[resource_type]} +{amount}"
                )
                player_gains = gains_by_player.setdefault(player, {})
                player_gains[resource_type] = (
                    player_gains.get(resource_type, 0) + amount
                )

            if (
                resource_type is ResourceType.WHEAT
                and granted_players
                and self.is_forecast_event_active(WHEAT_HARVEST_EVENT_ID)
            ):
                bonus_required = len(granted_players)
                if self.bank.available(ResourceType.WHEAT) >= bonus_required:
                    for player in granted_players:
                        self.give_resource_from_bank(
                            player,
                            ResourceType.WHEAT,
                            1,
                        )
                        gain_summaries.append(f"{player.name}: 麦 +1（豊作）")
                        player_gains = gains_by_player.setdefault(player, {})
                        player_gains[ResourceType.WHEAT] = (
                            player_gains.get(ResourceType.WHEAT, 0) + 1
                        )
                    self.add_log(
                        "豊作ボーナス: 麦を生産した各プレイヤーが追加で1枚獲得しました。"
                    )
                else:
                    self.add_log(
                        "豊作ボーナスは銀行の麦不足により配布されませんでした。"
                    )
                forecast_state = self.get_variant_component_state(
                    FORECAST_EVENTS_KIND
                )
                next_state, _consumed = forecast_state.consume_forecast_effect(
                    WHEAT_HARVEST_EVENT_ID
                )
                self.replace_variant_component_state(
                    FORECAST_EVENTS_KIND,
                    next_state,
                )
        for player, bundle in gains_by_player.items():
            self.last_resource_distribution[player.name] = dict(bundle)
            self.record_public_gain(player, bundle, f"出目{dice_roll}")
        return gain_summaries

    def update(self):
        if self.is_pre_game_settings_open():
            self.buttons = []
            return
        if self.is_lan_overlay_open():
            self.lan_lobby_flow.update()
            if self.lan_lobby_flow.match_active and not self.lan_match_seen:
                self.lan_match_seen = True
                self.lan_match_visible = True
            self.sync_lan_build_selection()
            self.buttons = []
            return
        if self.replay_mode:
            self.update_replay()
            self.buttons = [] if self.headless else self.build_buttons()
            return
        self.update_dice_animation()
        self.update_ai()
        self.flush_replay_capture()
        self.buttons = [] if self.headless else self.build_buttons()

    def render(self):
        if self.headless:
            raise RuntimeError("ヘッドレスゲームは描画できません。")
        if self.is_pre_game_settings_open():
            self.buttons = []
            self.pre_game_settings_layout = draw_pre_game_settings_display(
                self.screen,
                self.get_pre_game_settings_display_state(),
            )
            pygame.display.flip()
            return
        if self.is_lan_overlay_open():
            flow = self.lan_lobby_flow
            self.buttons = []
            if (
                flow.match_active
                and flow.mode == "connected"
                and self.lan_match_visible
            ):
                state = self.get_lan_match_display_state()
                if state is not None:
                    self.lan_match_layout = draw_lan_match_display(
                        self.screen,
                        state,
                    )
                    self.lan_lobby_layout = None
                else:
                    self.lan_match_layout = None
            else:
                self.lan_lobby_layout = draw_lan_lobby_display(
                    self.screen,
                    self.get_lan_lobby_display_state(),
                )
                self.lan_match_layout = None
            pygame.display.flip()
            return
        self.buttons = self.build_buttons()
        if self.phase == "finished" and not self.replay_mode:
            if self.match_result is None:
                self.refresh_match_result()
            self.result_display_layout = draw_result_display(
                self.screen,
                self.match_result,
                self.result_selected_event_index,
            )
            if self.show_help_panel:
                draw_help_panel(
                    self.screen,
                    "リザルト操作",
                    [
                        "↑ / ↓ またはホイール: 重要イベントを選択",
                        "Enter / Space: 選択イベントからリプレイ",
                        "R: 同じ盤面で再戦 / N: 新しい盤面",
                        "L: 対局ログを表示 / H: このヘルプを閉じる",
                    ],
                    "運指数は100が盤面確率どおりです。",
                    collapsed=False,
                )
            if self.show_log_panel:
                draw_log(
                    self.screen,
                    self.log_messages,
                    panel_height=SCREEN_HEIGHT - 24,
                    latest_event=self.latest_event,
                    expanded=True,
                    scroll_offset=self.log_scroll_offset,
                )
            draw_transient_message(self.screen, self.get_active_feedback())
            pygame.display.flip()
            return
        self.screen.fill(COLORS["BACKGROUND"])
        draw_ocean_background(self.screen)
        revealed_tiles = None
        visible_harbors = None
        if self.is_frontier_variant():
            revealed_tiles = [
                tile for tile in self.board.tiles if self.is_frontier_tile_revealed(tile)
            ]
            visible_harbors = [
                harbor
                for harbor in self.board.harbors
                if self.is_frontier_harbor_revealed(harbor)
            ]
        self.board.draw(
            self.screen,
            revealed_tiles=revealed_tiles,
            visible_harbors=visible_harbors,
        )
        if self.replay_mode:
            highlight_data = {
                "settlement_nodes": [],
                "city_nodes": [],
                "target_nodes": [],
                "edge_highlights": [],
                "tile_highlights": [],
            }
        else:
            highlight_data = self.get_board_highlight_data()
        draw_board_highlights(
            self.screen,
            settlement_nodes=highlight_data["settlement_nodes"],
            city_nodes=highlight_data["city_nodes"],
            target_nodes=highlight_data["target_nodes"],
            edge_highlights=highlight_data["edge_highlights"],
            tile_highlights=highlight_data["tile_highlights"],
        )
        help_collapsed = not self.show_help_panel
        log_height = LOG_PANEL_HEIGHT
        if help_collapsed:
            collapsed_help_y = (
                HELP_PANEL_Y + HELP_PANEL_HEIGHT - HELP_PANEL_COLLAPSED_HEIGHT
            )
            log_height = collapsed_help_y - 12 - 14
        draw_log(
            self.screen,
            self.log_messages,
            panel_height=log_height,
            latest_event=self.latest_event,
            expanded=self.show_log_panel,
            scroll_offset=self.log_scroll_offset,
            compact_label=(
                f"リプレイ {self.replay_index + 1}/{len(self.replay_archive.frames)}  V:手札  Esc:終了"
                if self.replay_mode and self.replay_archive is not None
                else None
            ),
        )
        if self.replay_mode:
            help_title = "リプレイ操作"
            help_lines = [
                "← / →: 1イベントずつ前後へ移動",
                "Home / End: 最初 / 最後のイベントへ移動",
                "Space: 自動再生 / 一時停止",
                "V: 全員の手札を公開 / 非公開",
                "Esc: 閲覧前の画面へ戻る",
            ]
            help_accent = (
                "リプレイは閲覧専用です。過去の状態から対局を再開することはできません。"
            )
        else:
            help_title, help_lines, help_accent = self.get_help_panel_content()
        draw_help_panel(
            self.screen, help_title, help_lines, help_accent, collapsed=help_collapsed
        )
        domestic_actor = (
            self.get_domestic_trade_actor() if self.is_domestic_trade_phase() else None
        )
        handoff_phase = self.special_phase in (
            "player_handoff",
            "domestic_trade_handoff",
            "domestic_trade_counter_handoff",
        )
        if self.replay_mode:
            visible_resource_player = None
        elif handoff_phase:
            visible_resource_player = None
        elif self.is_domestic_trade_phase():
            visible_resource_player = None if handoff_phase else domestic_actor
        elif (
            self.phase == "initial"
            and self.initial_dice_phase
            and self.initial_dice_contenders
        ):
            visible_resource_player = self.initial_dice_contenders[
                self.initial_player_index
            ]
        elif self.phase == "initial" and self.initial_placement_order:
            visible_resource_player = self.initial_placement_order[
                self.initial_player_index
            ]
        else:
            visible_resource_player = (
                self.discard_player
                if self.special_phase == "discard"
                else self.get_current_player()
            )
        if visible_resource_player is not None and visible_resource_player.is_ai:
            visible_resource_player = None
        board_active_player = (
            self.get_current_player()
            if self.phase == "main"
            else visible_resource_player
        )
        if self.special_phase == "player_handoff":
            board_active_player = self.handoff_player
        if self.replay_mode and self.replay_reveal_all:
            display_points = {
                player.name: self.get_player_victory_points(player)
                for player in self.players
            }
        elif self.replay_mode:
            display_points = {
                player.name: self.get_player_public_victory_points(player)
                for player in self.players
            }
        else:
            display_points = self.get_points_by_player()
        draw_resource_counts(
            self.screen,
            self.players,
            points_by_player=display_points,
            longest_road_owner=self.longest_road_owner,
            largest_army_owner=self.largest_army_owner,
            visible_player=visible_resource_player,
            reveal_all=(self.phase == "finished" and not self.replay_mode)
            or (self.replay_mode and self.replay_reveal_all),
            current_player=board_active_player,
            public_gain_by_player={
                player.name: self.get_recent_public_gain_text(player)
                for player in self.players
            },
            victory_point_target=self.victory_point_target,
            hide_ai_personalities=self.ai_personality_mode == MIXED,
        )
        if self.replay_mode:
            tracker_title, tracker_subtitle, tracker_steps = (
                "リプレイビューアー",
                "閲覧専用",
                [],
            )
        else:
            progress = self.get_progress_header_data()
            draw_progress_header(
                self.screen,
                progress["title"],
                progress["instruction"],
                progress["steps"],
                actor_color=progress["actor_color"],
                is_ai=progress["is_ai"],
            )
            tracker_title, tracker_subtitle, tracker_steps = (
                self.get_phase_tracker_data()
            )
        panel_title = "操作パネル"
        panel_subtitle = ""
        if self.replay_mode:
            panel_title = "リプレイビューアー"
            visibility = (
                "全手札を公開中" if self.replay_reveal_all else "公開情報のみ表示"
            )
            panel_subtitle = f"閲覧専用 / {visibility}"
        elif self.special_phase == "player_handoff":
            panel_title = "プレイヤー交代"
            panel_subtitle = f"{self.handoff_player.name} / {self.handoff_context}"
        elif self.phase == "initial" and self.initial_dice_phase:
            panel_title = "初期設定"
            panel_subtitle = "人数・勝利点・盤面を確認して開始"
        elif self.phase == "initial":
            panel_title = "初期配置"
            current_player = self.initial_placement_order[self.initial_player_index]
            if self.waiting_for_road:
                panel_subtitle = (
                    f"{current_player.name}: 開拓地に隣接する辺へ街道を配置"
                )
            else:
                panel_subtitle = (
                    f"{current_player.name}: 開拓地を配置 (Round {self.initial_round})"
                )
        elif self.phase == "finished":
            panel_title = "ゲーム終了"
            if self.winner is not None:
                panel_subtitle = f"{self.winner.name} の勝利 — R:再戦 / N:新規盤面"
        elif self.phase == "main":
            current_player = self.get_current_player()
            if current_player is not None:
                panel_subtitle = f"現在の手番: {current_player.name}"
                if self.is_domestic_trade_phase():
                    panel_title = "国内交易"
                    panel_subtitle = self.get_domestic_trade_subtitle()
                elif (
                    self.special_phase == "discard" and self.discard_player is not None
                ):
                    panel_subtitle = f"捨て札中: {self.discard_player.name} 残り {self.discard_remaining}"
                elif self.special_phase == "move_robber":
                    panel_subtitle = f"{current_player.name}: 盗賊の移動先を選択"
                elif self.special_phase == "steal":
                    panel_subtitle = f"{current_player.name}: 略奪対象を選択"
                elif self.special_phase == "bank_trade_give":
                    panel_subtitle = "銀行交易: 支払う資源を選択"
                elif self.special_phase == "bank_trade_receive":
                    panel_subtitle = "銀行交易: 受け取る資源を選択"
                elif self.special_phase == "year_of_plenty":
                    panel_subtitle = "収穫: 1-5 で資源を2枚選択"
                elif self.special_phase == "monopoly":
                    panel_subtitle = "独占: 1-5 で資源種類を指定"
                elif self.special_phase == "road_building":
                    panel_subtitle = f"街道建設: 残り {self.free_roads_remaining} 本"
                elif not self.dice_rolled:
                    panel_subtitle = f"現在の手番: {current_player.name} / ダイス前"
                elif self.action_mode is not None:
                    panel_subtitle = f"現在の手番: {current_player.name} / 建設先を選択"
                else:
                    panel_subtitle = f"現在の手番: {current_player.name} / 行動中"
        if not panel_subtitle:
            panel_subtitle = tracker_subtitle
        panel_player = self.get_current_player() if self.phase == "main" else None
        if self.replay_mode:
            panel_player = None
        elif self.special_phase == "player_handoff":
            panel_player = None
        elif self.is_domestic_trade_phase():
            panel_player = None if handoff_phase else domestic_actor
        current_player = self.get_current_player()
        show_main_details = bool(
            self.phase == "main"
            and not self.replay_mode
            and self.special_phase is None
            and current_player is not None
            and not current_player.is_ai
        )
        draw_side_panel(
            self.screen,
            panel_title,
            panel_subtitle,
            tracker_steps,
            ([] if self.replay_mode else self.get_side_panel_guidance()),
            panel_player,
            self.players,
            self.buttons,
            display_points,
            self.get_all_point_breakdowns(),
            self.get_trade_rates(self.get_current_player())
            if show_main_details
            else {},
            self.get_current_player_development_summary() if show_main_details else "",
            len(self.development_deck),
            self.get_build_affordability(self.get_current_player())
            if show_main_details and self.get_current_player()
            else [],
            self.get_pre_game_board_summary()
            if not self.replay_mode
            and self.phase == "initial"
            and self.initial_dice_phase
            else None,
            bank_resources=dict(self.bank.resources),
        )
        if self.replay_mode and self.replay_archive is not None:
            current_frame = self.get_current_replay_frame()
            event_title = self.latest_event.get("title", "")
            event_detail = self.latest_event.get("detail", "")
            if not event_title and current_frame is not None:
                event_title = current_frame.label
            draw_replay_status_card(
                self.screen,
                pygame.Rect(
                    SIDE_PANEL_X + 14,
                    82,
                    SIDE_PANEL_WIDTH - 28,
                    122,
                ),
                event_title,
                event_detail,
                self.replay_index,
                len(self.replay_archive.frames),
                is_playing=self.replay_playing,
                keyboard_hint="Space 再生/停止  ←/→ 前後  V 手札  Esc 終了",
            )
        draw_transient_message(self.screen, self.get_active_feedback())
        if self.dice_overlay.is_active:
            self.dice_overlay.draw(self.screen)
        pygame.display.flip()

    def run(self):
        if self.headless:
            raise RuntimeError("ヘッドレスゲームは対話ループを開始できません。")
        try:
            while self.running:
                self.handle_events()
                if not self.running:
                    break
                self.update()
                self.render()
                self.clock.tick(60)
        finally:
            if self.lan_lobby_flow is not None:
                self.close_lan_lobby(permanent=True)
            self.audio.stop()
            pygame.quit()


if __name__ == "__main__":
    game = CatanGame()
    game.run()
