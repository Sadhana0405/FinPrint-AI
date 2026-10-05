import os
import sys
import logging

import mysql.connector
from mysql.connector import Error

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logger = logging.getLogger(__name__)


def get_connection():
    """Open a MySQL connection using settings from config.py / .env."""
    try:
        connection = mysql.connector.connect(
            host=config.MYSQL_HOST,
            port=config.MYSQL_PORT,
            user=config.MYSQL_USER,
            password=config.MYSQL_PASSWORD,
            database=config.MYSQL_DATABASE,
        )
        return connection
    except Error as error:
        logger.error("Could not connect to MySQL: %s", error)
        return None


def close_connection(connection):
    """Close the connection if it is still open."""
    if connection is not None and connection.is_connected():
        connection.close()


if __name__ == "__main__":
    connection = get_connection()
    if connection is not None and connection.is_connected():
        print("MySQL connection successful.")
        close_connection(connection)
    else:
        print("MySQL connection failed.")
