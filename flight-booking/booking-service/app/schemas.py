import datetime as dt
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints


class BookingStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


UserId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Iata = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{3}$")]


class BookingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UserId
    flight_id: UUID
    passenger_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    passenger_email: EmailStr
    seat_count: int = Field(ge=1, le=50)


class Booking(BaseModel):
    id: UUID
    user_id: str
    flight_id: UUID
    passenger_name: str
    passenger_email: str
    seat_count: int
    total_price: float
    currency: str = "RUB"
    status: BookingStatus
    created_at: datetime
    updated_at: datetime


class AirportOut(BaseModel):
    iata_code: str
    name: str
    city: str


class AirlineOut(BaseModel):
    iata_code: str
    name: str


class FlightOut(BaseModel):
    id: UUID
    flight_number: str
    airline: AirlineOut
    origin: AirportOut
    destination: AirportOut
    departure_time: datetime
    arrival_time: datetime
    total_seats: int
    available_seats: int
    price: float
    currency: str
    status: str


class FlightSearchParams(BaseModel):
    origin: Iata
    destination: Iata
    date: dt.date | None = None


def money(amount_minor: int) -> Decimal:
    return Decimal(amount_minor) / 100
