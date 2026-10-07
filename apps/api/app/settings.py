from pathlib import Path
from typing import Annotated
import os
import yaml
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[3]

Positive = Annotated[float, Field(gt=0)]

class Screen(BaseModel):
    min_traded_value_krw: Positive = 10_000_000_000
    candidate_limit: int = Field(100, ge=1, le=500)
    min_recommend_score: float = Field(80, ge=0, le=100)

class SignificantCandle(BaseModel):
    min_gain_pct: Positive = 4
    min_volume_ratio_20d: Positive = 1.8
    close_position_min: float = Field(.65, ge=0, le=1)
    lookback_days: int = Field(10, ge=1, le=30)
    holding_volume_ratio: float = Field(.65, gt=0, le=1)
    holding_price_ratio: float = Field(.99, gt=0, le=1)

class MovingAverage(BaseModel):
    short: int = Field(5, ge=2, le=20)
    medium: int = Field(20, ge=5, le=60)
    @model_validator(mode='after')
    def ordered(self):
        if self.medium <= self.short:
            raise ValueError('medium must exceed short')
        return self

class Box(BaseModel):
    lookback_days: int = Field(24, ge=5, le=30)
    touch_tolerance_pct: float = Field(.8, gt=0, le=5)
    min_touches: int = Field(2, ge=2, le=10)

class Entry(BaseModel):
    ma5_distance_pct: float = Field(1.2, gt=0, le=10)
    pivot_reclaim_confirm_pct: float = Field(.15, ge=0, le=3)
    confirmation_minutes: int = Field(3, ge=1, le=30)
    volume_acceleration: Positive = 1.5
    zone_width_pct: float = Field(.5, gt=0, le=5)
    support_tolerance_pct: float = Field(1, gt=0, le=5)

class Risk(BaseModel):
    max_stop_loss_pct: float = Field(2, gt=0, le=20)
    min_risk_reward: float = Field(1.8, ge=1.5, le=10)
    stop_buffer_pct: float = Field(.2, gt=0, le=5)
    target_r_multiple: float = Field(3, ge=1.5, le=10)

class Theme(BaseModel):
    min_constituents: int = Field(3, ge=3, le=50)
    min_breadth_pct: float = Field(55, ge=0, le=100)
    min_strength: float = Field(60, ge=0, le=100)

class Weights(BaseModel):
    theme_strength: float = Field(25, ge=0, le=100)
    money_flow: float = Field(20, ge=0, le=100)
    chart_structure: float = Field(25, ge=0, le=100)
    support_confluence: float = Field(15, ge=0, le=100)
    risk_reward: float = Field(10, ge=0, le=100)
    realtime_confirmation: float = Field(5, ge=0, le=100)
    @model_validator(mode='after')
    def sum_100(self):
        if abs(sum(self.model_dump().values()) - 100) > .001:
            raise ValueError('weights must sum to 100')
        return self

class Backtest(BaseModel):
    forward_bars: int = Field(5, ge=1, le=30)
    fee_pct: float = Field(.015, ge=0, le=2)
    slippage_pct: float = Field(.05, ge=0, le=2)

class StrategyConfig(BaseModel):
    screen: Screen = Field(default_factory=Screen)
    significant_candle: SignificantCandle = Field(default_factory=SignificantCandle)
    moving_average: MovingAverage = Field(default_factory=MovingAverage)
    box: Box = Field(default_factory=Box)
    entry: Entry = Field(default_factory=Entry)
    risk: Risk = Field(default_factory=Risk)
    theme: Theme = Field(default_factory=Theme)
    weights: Weights = Field(default_factory=Weights)
    backtest: Backtest = Field(default_factory=Backtest)

def runtime_dir() -> Path:
    path = Path(os.environ.get('API_DATA_DIR', str(ROOT / '.runtime')))
    path.mkdir(parents=True, exist_ok=True)
    return path

def read_strategy() -> StrategyConfig:
    override = runtime_dir() / 'strategy.yml'
    path = override if override.exists() else ROOT / 'config' / 'strategy.yml'
    return StrategyConfig.model_validate(yaml.safe_load(path.read_text()))

def read_themes() -> list[dict]:
    override = runtime_dir() / 'themes.yml'
    path = override if override.exists() else ROOT / 'config' / 'themes.yml'
    return yaml.safe_load(path.read_text())['themes']

def save_yaml(name: str, value: dict):
    path = runtime_dir() / name
    temporary = path.with_suffix('.tmp')
    temporary.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
    temporary.replace(path)
