import logging
import os
import json
from logging.handlers import RotatingFileHandler
from typing import Dict, Any, Optional


class GuildFormatter(logging.Formatter):
    """Custom formatter that handles guild parameter, defaulting to 'Core' if not provided."""
    
    def format(self, record: logging.LogRecord) -> str:
        # Set default guild to "Core" if not provided
        if not hasattr(record, 'guild') or record.guild is None:
            record.guild = "Core"
        return super().format(record)


class CustomLogger:
    """
    Custom logger that sends output to multiple files based on log levels:
    - Errors.log: CRITICAL and ERROR only
    - Log.log: INFO, WARNING, ERROR, CRITICAL
    - Latest.log: Everything, cleared on startup (current instance only)
    - debug.log: DEBUG only
    - stdout: DEBUG only
    """
    
    def __init__(self, config_path: str = "logger_config.json"):
        self.config = self._load_config(config_path)
        self.log_directory = self.config.get("log_directory", "logs")
        self._ensure_log_directory()
        self.logger = logging.getLogger("DiscordCombatAI")
        self.logger.setLevel(logging.DEBUG)
        
        # Clear existing handlers
        self.logger.handlers.clear()
        
        # Setup file handlers
        self._setup_file_handlers()
        
        # Setup console handler
        if self.config.get("console", {}).get("enabled", True):
            self._setup_console_handler()
    
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load logger configuration from JSON file."""
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Logger configuration file not found: {config_path}")
        
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    def _ensure_log_directory(self):
        """Ensure the log directory exists."""
        if not os.path.exists(self.log_directory):
            os.makedirs(self.log_directory, exist_ok=True)
    
    def _get_file_path(self, filename: str) -> str:
        """Get full path for a log file."""
        return os.path.join(self.log_directory, filename)
    
    def _setup_file_handlers(self):
        """Setup all file handlers based on configuration."""
        file_format = GuildFormatter(
            self.config.get("format", {}).get("file", "[%(asctime)s][%(name)s][%(levelname)s][%(guild)s]: %(message)s"),
            datefmt=self.config.get("date_format", "%Y-%m-%d %H:%M:%S")
        )
        
        files_config = self.config.get("files", {})
        
        for handler_name, handler_config in files_config.items():
            filename = handler_config.get("filename")
            levels = handler_config.get("levels", [])
            clear_on_startup = handler_config.get("clear_on_startup", False)
            
            file_path = self._get_file_path(filename)
            
            # Clear file if it's Latest.log (current instance only)
            if clear_on_startup and os.path.exists(file_path):
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("")  # Clear the file
            
            # Create handler
            handler = RotatingFileHandler(
                file_path,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
                encoding="utf-8"
            )
            handler.setFormatter(file_format)
            
            # Set level based on the minimum level in the levels list
            level_mapping = {
                "DEBUG": logging.DEBUG,
                "INFO": logging.INFO,
                "WARNING": logging.WARNING,
                "ERROR": logging.ERROR,
                "CRITICAL": logging.CRITICAL
            }
            
            # Find minimum level
            min_level = logging.DEBUG
            if levels:
                min_level = min(level_mapping.get(level, logging.DEBUG) for level in levels)
            
            handler.setLevel(min_level)
            
            # Add custom filter to only allow specified levels
            class LevelFilter(logging.Filter):
                def __init__(self, allowed_levels):
                    super().__init__()
                    self.allowed_levels = [level_mapping.get(level, logging.DEBUG) for level in allowed_levels]
                
                def filter(self, record):
                    return record.levelno in self.allowed_levels
            
            handler.addFilter(LevelFilter(levels))
            self.logger.addHandler(handler)
    
    def _setup_console_handler(self):
        """Setup console handler for DEBUG level only."""
        console_config = self.config.get("console", {})
        console_format = GuildFormatter(
            self.config.get("format", {}).get("console", "[%(asctime)s][%(name)s][%(levelname)s][%(guild)s]: %(message)s"),
            datefmt=self.config.get("date_format", "%Y-%m-%d %H:%M:%S")
        )
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(console_format)
        console_handler.setLevel(logging.DEBUG)
        
        # Filter to only show DEBUG level
        class DebugOnlyFilter(logging.Filter):
            def filter(self, record):
                return record.levelno == logging.DEBUG
        
        console_handler.addFilter(DebugOnlyFilter())
        self.logger.addHandler(console_handler)
    
    def get_logger(self) -> logging.Logger:
        """Get the configured logger instance."""
        return self.logger
    
    def debug(self, message: str):
        """Log a debug message."""
        self.logger.debug(message)
    
    def info(self, message: str):
        """Log an info message."""
        self.logger.info(message)
    
    def warning(self, message: str):
        """Log a warning message."""
        self.logger.warning(message)
    
    def error(self, message: str):
        """Log an error message."""
        self.logger.error(message)
    
    def critical(self, message: str):
        """Log a critical message."""
        self.logger.critical(message)


# Global logger instance
_logger_instance = None


def get_logger(config_path: str = "logger_config.json") -> logging.Logger:
    """
    Get or create the global logger instance.
    If logger hasn't been initialized, it will be initialized automatically.
    
    Args:
        config_path: Path to the logger configuration file (only used if logger not yet initialized)
        
    Returns:
        Configured logger instance
    """
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = CustomLogger(config_path)
    return _logger_instance.get_logger()


def log_with_guild(logger: logging.Logger, level: str, message: str, guild: Optional[Any] = None):
    """
    Helper function to log with guild information.
    
    Args:
        logger: The logger instance
        level: Log level (debug, info, warning, error, critical)
        message: Log message
        guild: Guild object (discord.Guild) or guild name/ID, or None for "Core"
    """
    # Extract guild identifier
    guild_name = "Core"
    if guild is not None:
        if hasattr(guild, 'name') and hasattr(guild, 'id'):
            # It's a discord.Guild object
            guild_name = f"{guild.name}({guild.id})"
        elif hasattr(guild, 'id'):
            # It has an id attribute
            guild_name = str(guild.id)
        else:
            # It's a string or number
            guild_name = str(guild)
    
    # Get the appropriate log method
    log_method = getattr(logger, level.lower(), logger.info)
    log_method(message, extra={"guild": guild_name})


def init_logger(config_path: str = "logger_config.json") -> CustomLogger:
    """
    Initialize the logger with the given configuration.
    
    Args:
        config_path: Path to the logger configuration file
        
    Returns:
        CustomLogger instance
    """
    global _logger_instance
    _logger_instance = CustomLogger(config_path)
    return _logger_instance

