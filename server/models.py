"""Various Peewee models."""
from __future__ import annotations

import datetime
import enum
import hashlib
import hmac
import os
import typing

import config

import peewee as pw

import playhouse.postgres_ext as pw_postgres


def hash_password(password: str) -> str:
    """Hash a password."""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha3-256', password.encode(), salt, 100_000)
    return salt + key


def check_password(password: str, hashed: str) -> bool:
    """Check a password against a hash."""
    salt = hashed[:32]
    key = hashed[32:]
    attempt_key = hashlib.pbkdf2_hmac(
        'sha3-256', password.encode(), salt, 100_000
    )
    return hmac.compare_digest(key, attempt_key)


db = pw_postgres.PostgresqlExtDatabase(
    config.DB_NAME, user=config.DB_USER, password=config.DB_PASSWORD
)


class HashedPassword:
    """A class to check for equality against hashed passwords."""

    def __init__(self, hashed_password: str):
        """Store the hashed password."""
        self.hashed_password = hashed_password

    def __eq__(self, password: str) -> bool:
        """Check for equality against an unhashed password."""
        return check_password(password, self.hashed_password)


class TurnCounter:
    """A counter for the turn of a game."""

    def __init__(self, game: Game):
        """Store game."""
        self.game = game

    def __int__(self) -> int:
        """Get the turn number."""
        # _turn_number is internal, but this is the class that changes it
        return self.game._turn_number

    def __str__(self) -> str:
        """Get the turn number as a string."""
        return str(self.game._turn_number)

    def __iadd__(self, value: int):
        """Increment the turn."""
        if value != 1:
            raise ValueError(
                'Cannot increment the turn counter by more than one.'
            )
        self.game._turn_number += 1
        self.game.current_turn = ~self.game.current_turn
        self.game.save()


class Winner(enum.Enum):
    """An enum for the winner of a game."""

    GAME_NOT_COMPLETE = enum.auto()
    HOME = enum.auto()
    AWAY = enum.auto()
    DRAW = enum.auto()


class Conclusion(enum.Enum):
    """An enum for the way a game finished."""

    GAME_NOT_COMPLETE = enum.auto()
    CHECKMATE = enum.auto()
    RESIGN = enum.auto()
    TIME = enum.auto()
    STALEMATE = enum.auto()
    THREEFOLD_REPETITION = enum.auto()
    FIFTY_MOVE_RULE = enum.auto()
    AGREED_DRAW = enum.auto()


class PieceType(enum.Enum):
    """An enum for a chess piece type."""

    PAWN = enum.auto()
    ROOK = enum.auto()
    KNIGHT = enum.auto()
    BISHOP = enum.auto()
    QUEEN = enum.auto()
    KING = enum.auto()


class Side(enum.Enum):
    """An enum for home/away."""

    HOME = enum.auto()
    AWAY = enum.auto()

    def __invert__(self) -> Side:
        """Get the other side."""
        if self == Side.HOME:
            return Side.AWAY
        return Side.HOME


class EnumField(pw.SmallIntegerField):
    """A field where each value is an integer representing an option."""

    def __init__(
            self, options: enum.Enum, **kwargs: typing.Dict[str, typing.Any]):
        """Create a new enum field."""
        self.options = options
        super().__init__(**kwargs)

    def python_value(self, raw: typing.Any) -> enum.Enum:
        """Convert a raw number to an enum value."""
        number = super().python_value(raw)
        return self.options(number)

    def db_value(self, instance: enum.Enum) -> typing.Any:
        """Convert an enum value to a raw number."""
        if not isinstance(instance, self.options):
            raise TypeError(f'Instance is not of enum class {self.options}.')
        number = instance.value
        return super().db_value(number)


class BaseModel(pw.Model):
    """A base model, that sets the DB."""

    class Meta:
        """Set the DB and use new table names."""

        database = db
        use_legacy_table_names = False

    def __str__(self, indent: int = 1) -> str:
        """Represent the model as a string."""
        values = {}
        for field in type(self)._meta.sorted_field_names:
            values[field] = getattr(self, field)
        main = []
        for field in values:
            if isinstance(values[field], datetime.datetime):
                value = f"'{values[field]}'"
            elif isinstance(values[field], pw.Model):
                value = values[field].__str__(indent=indent + 1)
            elif isinstance(values[field], enum.Enum):
                value = values[field].name
            else:
                value = repr(values[field])
            main.append(f'{field}={value}')
        end_indent = '    ' * (indent - 1)
        indent = '\n' + '    ' * indent
        return (
            f'<{type(self).__name__}{indent}'
            + indent.join(main)
            + f'\n{end_indent}>'
        )


class User(BaseModel):
    """A model to represent a user."""

    username = pw.CharField(max_length=32, unique=True)
    password_hash = pw.BlobField()
    email = pw.CharField(max_length=255, unique=True)
    email_verified = pw.BooleanField(default=False)
    elo = pw.SmallIntegerField(default=1000)
    avatar = pw.BlobField(null=True)
    created_at = pw.DateTimeField(default=datetime.datetime.now)

    @property
    def password(self) -> HashedPassword:
        """Return an object that will use hashing in it's equality check."""
        return HashedPassword(self.password_hash)

    @password.setter
    def password(self, password: str):
        """Set the password to a hash of the provided password."""
        self.password_hash = hash_password(password)


class Game(BaseModel):
    """A model to represent a game.

    The game may be in any of the following states:
      1. Open
          A player is looking for a game matching these specs, but a second
          player has yet to be found.
      2. In progress
          There are two players in this game, who are currently playing.
      3. Completed
          This game has ended - either there is a winner, or it was a draw.
    """

    host = pw.ForeignKeyField(model=User, backref='games')
    away = pw.ForeignKeyField(model=User, backref='games', null=True)
    current_turn = EnumField(Side, default=Side.HOME)
    _turn_number = pw.SmallIntegerField(default=1, column_name='turn_number')
    mode = pw.SmallIntegerField(default=1)         # only valid value for now
    starting_time = pw_postgres.IntervalField()    # initial timer value
    time_per_turn = pw_postgres.IntervalField()    # time incremement per turn

    # timers at the start of the current turn, null means starting_time
    home_time = pw_postgres.IntervalField(null=True)
    away_time = pw_postgres.IntervalField(null=True)

    home_offering_draw = pw.BooleanField(default=False)
    away_offering_draw = pw.BooleanField(default=False)
    winner = EnumField(Winner, default=Winner.GAME_NOT_COMPLETE)
    conclusion_type = EnumField(
        Conclusion, default=Conclusion.GAME_NOT_COMPLETE
    )
    opened_at = pw.DateTimeField(default=datetime.datetime.now)
    last_turn = pw.DateTimeField(null=True)
    started_at = pw.DateTimeField(null=True)
    ended_at = pw.DateTimeField(null=True)

    def __init__(
            self, *args: typing.Tuple[typing.Any],
            **kwargs: typing.Dict[str, typing.Any]):
        """Create a game."""
        super().__init__(*args, **kwargs)
        self.turn_number = TurnCounter(self)
        self.home_time = self.starting_time
        self.away_time = self.starting_time

    def start_game(self, away: User):
        """Start a game which had no away side."""
        self.away = away
        self.started_at = datetime.datetime.now()
        self.last_turn = datetime.datetime.now()
        self.save()


class Piece(BaseModel):
    """A model to represent a piece in a game."""

    piece_type = EnumField(PieceType)
    file = pw.SmallIntegerField()
    rank = pw.SmallIntegerField()
    side = EnumField(Side)
    has_moved = pw.BooleanField(default=False)
    game = pw.ForeignKeyField(model=Game, backref='pieces')


db.create_tables([User, Game, Piece])
