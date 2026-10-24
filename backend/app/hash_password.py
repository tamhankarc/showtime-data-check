"""Interactive password hashing; never accepts a password on the command line."""
from getpass import getpass
from .security import password_hash


if __name__ == "__main__":
    first = getpass("Password (at least 12 characters): ")
    second = getpass("Repeat password: ")
    if first != second:
        raise SystemExit("Passwords do not match.")
    print(password_hash(first))
