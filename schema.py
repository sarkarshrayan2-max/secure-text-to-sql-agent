# schemas.py
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Dict, Any, Optional
from datetime import datetime

class UserPayload(BaseModel): #validating a single user data 
    name: str = Field(..., description="The full name of the employee/user.")
    role: Literal["Admin", "User", "Manager"] = Field(..., description="The security role of the user.")
    department: Literal["Engineering", "HR", "Sales", "Marketing", "Finance"] = Field(
        ..., description="The department the user belongs to for analytical groupings."
    )
    salary: float = Field(..., description="The annual salary of the user. Must be a positive value.")
    join_date: str = Field(
    default_factory=lambda: datetime.now().strftime("%Y-%m-%d"),
    description="Employee joining date in YYYY-MM-DD format."
)

    
    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if len(value) < 2:
            raise ValueError("Name must be at least 2 characters long.")
        return value.title()

    
    @field_validator("salary")
    @classmethod
    def validate_salary(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Salary must be a positive number greater than 0.")
        return round(value, 2)

    
    @field_validator("join_date")
    @classmethod
    def validate_join_date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return value
        except ValueError:
            raise ValueError(
                "join_date must be in valid YYYY-MM-DD text format."
        )


class DatabaseWriteAction(BaseModel):
    action_type: Literal["SELECT", "INSERT", "UPDATE", "ALTER"] = Field(
        ...,
        description="The type of database operation."
    )
    target_table: Literal["users"] = Field(
        default="users",
        description="This demo currently supports only the users table."
    )
    payload: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Fields for INSERT or UPDATE."
    )
    alter_statement: Optional[str] = Field(
        default=None,
        description="Raw ALTER SQL only for ALTER actions."
    )