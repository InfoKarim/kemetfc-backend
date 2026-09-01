from dataclasses import dataclass
from datetime import date, datetime
from app.physical_profile import PhysicalProfile
from app.technical_profile import TechnicalProfile
from app.mental_profile import MentalProfile
from app.tactical_profile import TacticalProfile
from app.match_performance import MatchPerformance
@dataclass
class Player:
    player_id: str
    first_name_ar: str
    last_name_ar: str
    first_name_en: str
    last_name_en: str
    date_of_birth: date
    sex: str
    physical_profile: PhysicalProfile
    technical_profile: TechnicalProfile
    mental_profile: MentalProfile
    match_performance: MatchPerformance
    tactical_profile: TacticalProfile
    team_id: str | None = None
    created_at: datetime | None = None
    photo_filename: str | None = None
