import logging


def logger_setup(logger_name="Congress Client", log_level=logging.INFO, propagate=False):
    """
    Set up and return a logger with the specified name and level.
    Avoids affecting the root logger by setting propagate to False.

    Args:
        logger_name (str): The name of the logger.
        log_level (int): The logging level (e.g., logging.INFO, logging.DEBUG).

    Returns:
        logger (logging.Logger): Configured logger instance.
    """
    # Retrieve or create a logger
    logger = logging.getLogger(logger_name)

    # Avoid adding duplicate handlers if already set up
    if not logger.hasHandlers():
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)  # Match handler level to logger level

        # Set the format for the handler
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s - raised_by: %(name)s',
            datefmt='%Y-%m-%d %H:%M:%S'
            )
        console_handler.setFormatter(formatter)

        # Add the handler to the logger
        logger.addHandler(console_handler)

    # Set the logger level explicitly and prevent it from propagating to the root
    logger.setLevel(log_level)
    logger.propagate = propagate

    return logger
