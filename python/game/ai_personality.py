from dataclasses import dataclass


STANDARD = "standard"
EXPANSION = "expansion"
TRADER = "trader"
DISRUPTOR = "disruptor"
MIXED = "mixed"


@dataclass(frozen=True)
class AIPersonalityProfile:
    """Public, immutable tuning values used by :class:`SimpleAI`."""

    key: str
    label: str
    description: str
    build_order: tuple[str, ...]
    goal_order: tuple[str, ...]
    road_score_threshold: float
    minimum_road_length: int
    development_before_road: bool
    domestic_trade_max_missing: int
    trade_reserve_relaxation: int
    trade_complete_ratio: float
    trade_improve_ratio: float
    trade_fair_ratio: float
    trade_counter_ratio: float
    monopoly_threshold: float
    knight_leader_threshold: int
    road_building_min_options: int
    pip_weight: float
    diversity_weight: float
    generic_harbor_bonus: float
    specific_harbor_bonus: float
    edge_spacing_bonus: float
    edge_lookahead_weight: float
    longest_road_bonus: float
    opponent_contact_bonus: float
    robber_self_penalty: float
    robber_production_weight: float
    robber_point_weight: float
    robber_leader_bonus: float
    robber_hand_weight: float


AI_PERSONALITY_PROFILES = {
    STANDARD: AIPersonalityProfile(
        key=STANDARD,
        label="標準",
        description="生産・建設・交易を均等に評価する標準型",
        build_order=("city", "settlement"),
        goal_order=("city", "settlement", "development", "road"),
        road_score_threshold=48,
        minimum_road_length=2,
        development_before_road=False,
        domestic_trade_max_missing=2,
        trade_reserve_relaxation=0,
        trade_complete_ratio=0.65,
        trade_improve_ratio=0.78,
        trade_fair_ratio=0.95,
        trade_counter_ratio=0.62,
        monopoly_threshold=6,
        knight_leader_threshold=7,
        road_building_min_options=2,
        pip_weight=4,
        diversity_weight=5,
        generic_harbor_bonus=5,
        specific_harbor_bonus=4,
        edge_spacing_bonus=10,
        edge_lookahead_weight=0.18,
        longest_road_bonus=6,
        opponent_contact_bonus=0,
        robber_self_penalty=8,
        robber_production_weight=4,
        robber_point_weight=2,
        robber_leader_bonus=5,
        robber_hand_weight=1,
    ),
    EXPANSION: AIPersonalityProfile(
        key=EXPANSION,
        label="拡大重視",
        description="開拓地と街道を優先し、将来の建設余地を広げる型",
        build_order=("settlement", "city"),
        goal_order=("settlement", "city", "road", "development"),
        road_score_threshold=34,
        minimum_road_length=3,
        development_before_road=False,
        domestic_trade_max_missing=2,
        trade_reserve_relaxation=0,
        trade_complete_ratio=0.74,
        trade_improve_ratio=0.86,
        trade_fair_ratio=1.02,
        trade_counter_ratio=0.72,
        monopoly_threshold=7,
        knight_leader_threshold=8,
        road_building_min_options=1,
        pip_weight=4.2,
        diversity_weight=6.5,
        generic_harbor_bonus=3,
        specific_harbor_bonus=3,
        edge_spacing_bonus=16,
        edge_lookahead_weight=0.34,
        longest_road_bonus=9,
        opponent_contact_bonus=0,
        robber_self_penalty=11,
        robber_production_weight=3,
        robber_point_weight=1.5,
        robber_leader_bonus=3,
        robber_hand_weight=0.5,
    ),
    TRADER: AIPersonalityProfile(
        key=TRADER,
        label="交渉重視",
        description="港と資源循環を重視し、幅広い条件で国内交易を行う型",
        build_order=("city", "settlement"),
        goal_order=("city", "settlement", "road", "development"),
        road_score_threshold=50,
        minimum_road_length=2,
        development_before_road=False,
        domestic_trade_max_missing=3,
        trade_reserve_relaxation=1,
        trade_complete_ratio=0.50,
        trade_improve_ratio=0.66,
        trade_fair_ratio=0.82,
        trade_counter_ratio=0.48,
        monopoly_threshold=5,
        knight_leader_threshold=7,
        road_building_min_options=2,
        pip_weight=3.8,
        diversity_weight=6,
        generic_harbor_bonus=12,
        specific_harbor_bonus=10,
        edge_spacing_bonus=9,
        edge_lookahead_weight=0.20,
        longest_road_bonus=4,
        opponent_contact_bonus=0,
        robber_self_penalty=10,
        robber_production_weight=3.5,
        robber_point_weight=1,
        robber_leader_bonus=2,
        robber_hand_weight=2,
    ),
    DISRUPTOR: AIPersonalityProfile(
        key=DISRUPTOR,
        label="妨害重視",
        description="発展カード・騎士・盗賊で得点上位を抑える型",
        build_order=("city", "settlement"),
        goal_order=("development", "city", "settlement", "road"),
        road_score_threshold=56,
        minimum_road_length=1,
        development_before_road=True,
        domestic_trade_max_missing=2,
        trade_reserve_relaxation=0,
        trade_complete_ratio=0.80,
        trade_improve_ratio=0.90,
        trade_fair_ratio=1.08,
        trade_counter_ratio=0.78,
        monopoly_threshold=4,
        knight_leader_threshold=4,
        road_building_min_options=2,
        pip_weight=4,
        diversity_weight=4,
        generic_harbor_bonus=4,
        specific_harbor_bonus=4,
        edge_spacing_bonus=8,
        edge_lookahead_weight=0.12,
        longest_road_bonus=5,
        opponent_contact_bonus=9,
        robber_self_penalty=7,
        robber_production_weight=6,
        robber_point_weight=4,
        robber_leader_bonus=12,
        robber_hand_weight=1,
    ),
}

AI_PERSONALITY_KEYS = tuple(AI_PERSONALITY_PROFILES)
AI_PERSONALITY_MODES = (STANDARD, MIXED, EXPANSION, TRADER, DISRUPTOR)

_ALIASES = {
    "balanced": STANDARD,
    "builder": EXPANSION,
    "blocker": DISRUPTOR,
}


def normalize_ai_personality(value):
    """Return a supported key, defaulting old/unknown values to ``standard``."""

    if not isinstance(value, str):
        return STANDARD
    key = value.strip().lower()
    key = _ALIASES.get(key, key)
    return key if key in AI_PERSONALITY_PROFILES else STANDARD


def get_ai_personality_profile(value):
    return AI_PERSONALITY_PROFILES[normalize_ai_personality(value)]


def normalize_ai_personality_mode(value):
    if not isinstance(value, str):
        return STANDARD
    key = value.strip().lower()
    if key == MIXED:
        return MIXED
    normalized = normalize_ai_personality(key)
    return normalized if key in AI_PERSONALITY_PROFILES or key in _ALIASES else STANDARD
