"""
custom_data_exceptions.py - Data layer exceptions.
"""


class MigrationNotFoundDB(Exception):
    def __init__(self, detail: str = "Migration not found"):
        self.detail = detail
        super().__init__(detail)


class MigrationAlreadyExistsDB(Exception):
    def __init__(self, detail: str = "Migration already exists"):
        self.detail = detail
        super().__init__(detail)


class GeneralDatabaseError(Exception):
    def __init__(self, detail: str = "Database error"):
        self.detail = detail
        super().__init__(detail)
