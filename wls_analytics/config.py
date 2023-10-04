# -*- coding: utf-8 -*-
# @author: Tomas Vitvar, https://vitvar.com, tomas@vitvar.com

import os
import logging
import logging.config
import warnings
import yaml
import re

from threading import Event

warnings.filterwarnings("ignore", category=DeprecationWarning)

from .utils import str2bool, deep_find, merge_dicts, Map
from functools import reduce

# they must be in a form ${VARIABLE_NAME}
ENVNAME_PATTERN = "[A-Z0-9_]+"
ENVPARAM_PATTERN = "\$\{%s\}" % ENVNAME_PATTERN

# consolidated variables supplied via env file and environment variables
ENV = {}

DEBUG = str2bool(os.getenv("WLSA_DEBUG", "False"))
ANSI_COLORS = not str2bool(os.getenv("WLSA_NO_ANSI", "False"))
TRACEBACK = str2bool(os.getenv("WLSA_TRACEBACK", "False"))
CONFIG_FILE = os.getenv("WLSA_CONFIG", None)

WLSA_HOME = os.getenv("WLSA_HOME", os.path.join(os.path.expanduser("~"), ".wls-analytics"))
DATA_DIR = os.path.join(WLSA_HOME, "data")


env_variables = {
    "WLSA_HOME": WLSA_HOME,
    "WLSA_CONFIG": CONFIG_FILE,
    "WLSA_DEBUG": DEBUG,
    "WLSA_TRACEBACK": TRACEBACK,
    "WLSA_NO_ANSI": not ANSI_COLORS,
}

# global exit event
exit_event = Event()


def get_schema_file(name):
    sfile = os.path.dirname(os.path.realpath(__file__)) + f"/schemas/{name}"
    if not os.path.exists(sfile):
        raise Exception(f"The schema {sfile} does not exist!")
    return sfile


def get_dir_path(config_dir, path, base_dir=None, check=False):
    """
    Return the directory for the path specified.
    """
    d = os.path.normpath((((config_dir if base_dir is None else base_dir) + "/") if path[0] != "/" else "") + path)
    if check and not os.path.exists(d):
        raise Exception(f"The directory {d} does not exist!")
    return d


def init_env(env_file, sep="=", comment="#"):
    """
    Read environment varialbes from the `env_file` and combines them with the OS environment variables.
    """
    env = {}
    for k, v in os.environ.items():
        env[k] = v
    if env_file:
        with open(env_file, "rt") as f:
            for line in f:
                l = line.strip()
                if l and not l.startswith(comment):
                    key_value = l.split(sep)
                    key = key_value[0].strip()
                    if not re.match(f"^{ENVNAME_PATTERN}$", key):
                        raise Exception(f"Invalid variable name '{key}'.")
                    value = sep.join(key_value[1:]).strip().strip("\"'")
                    env[key] = value
    return env


def read_config(config_file, env_file, scope=None):
    if not (os.path.exists(config_file)):
        raise Exception(f"The configuration file {config_file} does not exist!")
    if env_file and not (os.path.exists(env_file)):
        raise Exception(f"The environment file {env_file} does not exist!")

    # init yaml reader
    global ENV
    ENV = init_env(env_file)
    yaml.add_implicit_resolver("!env", re.compile(r".*%s.*" % ENVPARAM_PATTERN))
    yaml.add_constructor("!env", env_constructor)

    config_file = os.path.realpath(config_file)
    stream = open(config_file, "r", encoding="utf-8")
    try:
        config = yaml.load(stream, Loader=yaml.FullLoader)
    except Exception as e:
        raise Exception(f"Error when reading the configuration file {config_file}: {str(e)}")
    finally:
        stream.close()
    config_dir = os.path.dirname(config_file)
    return config, config_file, config_dir


def replace_env_variable(value):
    """
    Replace all environment varaibles in a string privided in `value` parameter
    with values of variable in `ENV` global variable.
    """
    params = list(set(re.findall("(%s)" % ENVPARAM_PATTERN, value)))
    if len(params) > 0:
        for k in params:
            env_value = ENV.get(k[2:-1])
            if env_value is None:
                raise Exception(f"The environment variable {k} does not exist!")
            else:
                value = value.replace(k, env_value)
    return value


def env_constructor(loader, node):
    """
    A constructor for environment varaibles provided in the yaml configuration file.
    It populates strings that contain environment variables in a form `${var_name}` with
    their values.
    """
    return replace_env_variable(node.value)


class Config:
    """
    The main confuguration.
    """

    def __init__(
        self,
        file,
        env=None,
        log_level="INFO",
        scope=None,
    ):
        """
        Read and parse the configuration from the yaml file and initializes the logging.
        """
        self.log_level = log_level
        if not (os.path.exists(file)):
            raise Exception(f"The configuration file {file} does not exist!")
        self.raw_config, self.config_file, self.config_dir = read_config(file, env, scope=scope)
        self.root = self.get_part(None)

    def get_dir_path(self, path, base_dir=None, check=False):
        """
        Return the full directory of the path with `config_dir` as the base directory.
        """
        return get_dir_path(self.config_dir, path, base_dir, check)

    def get_part(self, path):
        """
        Return a `ConfigPart` object for a part of the configuration
        """
        return ConfigPart(
            self,
            path,
            self.raw_config,
            self.config_dir,
        )

    def __call__(self, path, default=None, type=None, required=True, no_eval=False):
        return self.root(path, default=default, type=type, required=required, no_eval=no_eval)


class ConfigPart:
    def __init__(self, parent, base_path, config, config_dir):
        self.parent = parent
        self.config_dir = config_dir
        self.base_path = base_path
        if base_path is not None:
            self._config = deep_find(config, base_path)
        else:
            self._config = config

    def get_dir_path(self, path, base_dir=None, check=False):
        return get_dir_path(self.config_dir, path, base_dir, check)

    def path(self, path):
        return "%s.%s" % (self.base_path, path) if self.base_path is not None else path

    def __call__(self, path, default=None, type=None, required=True, no_eval=False):
        return self.value(path, default, type, required, no_eval)

    def value(self, path, default=None, type=None, required=True, no_eval=False):
        required = default is not None and required
        r = default
        if self._config is not None:
            val = reduce(
                lambda di, key: di.get(key, default) if isinstance(di, dict) else default,
                path.split("."),
                self._config,
            )
            if val == default:
                r = default
            else:
                if not no_eval:
                    if callable(getattr(val, "eval", None)):
                        try:
                            val = val.eval(
                                merge_dicts(
                                    self.parent.custom_functions,
                                    self.parent.scope,
                                )
                            )
                        except Exception as e:
                            raise Exception(
                                "Cannot evaluate Python expression for property '%s'. %s" % (self.path(path), str(e))
                            )
                r = type(val) if type != None else val
        if not r and required:
            raise Exception("The property '%s' does not exist!" % (self.path(path)))
        return Map(r) if isinstance(r, dict) else r

    def value_str(self, path, default=None, regex=None, required=False):
        v = self.value(path, default=default, type=str, required=required)
        if regex is not None and not re.match(regex, v):
            raise Exception("The property %s value %s does not match %s!" % (self.path(path), v, regex))
        return v

    def value_int(self, path, default=None, min=None, max=None, required=False):
        v = self.value(path, default=default, type=int, required=required)
        if min is not None and v < min:
            raise Exception("The property %s value %s must be greater or equal to %d!" % (self.path(path), v, min))
        if max is not None and v > max:
            raise Exception("The property %s value %s must be less or equal to %d!" % (self.path(path), v, max))
        return v

    def value_bool(self, path, default=None, required=False):
        return self.value(path, default=default, type=bool, required=required)


class CustomFormatter(logging.Formatter):
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format_header = "%(asctime)s [%(name)-14.14s] "
    format_msg = "[%(levelname)-1.1s] %(message)s"

    FORMATS = {
        logging.DEBUG: format_header + grey + format_msg + reset,
        logging.INFO: format_header + grey + format_msg + reset,
        logging.WARNING: format_header + yellow + format_msg + reset,
        logging.ERROR: format_header + red + format_msg + reset,
        logging.CRITICAL: format_header + bold_red + format_msg + reset,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


traceback_manager = logging.Manager(logging.RootLogger(logging.INFO))
traceback_handler = None


def init_logging(logs_dir, command_name, handlers=["file", "console"]):
    """
    Initialize the logging, set the log level and logging directory.
    """
    log_level = "DEBUG" if DEBUG else "INFO"
    os.makedirs(logs_dir, exist_ok=True)

    # main logs configuration
    logging_dict = {
        "version": 1,
        "disable_existing_loggers": True,
        "formatters": {
            "standard": {"format": CustomFormatter.format_header + CustomFormatter.format_msg},
            "colored": {"()": CustomFormatter},
        },
        "handlers": {
            "console": {
                "formatter": "colored" if ANSI_COLORS else "standard",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",  # Default is stderr
            },
            "file": {
                "formatter": "standard",
                "class": "logging.handlers.TimedRotatingFileHandler",
                "filename": f"{logs_dir}/wlsa-{command_name}.log",
                "when": "midnight",
                "interval": 1,
                "backupCount": 30,
            },
        },
        "loggers": {
            "": {  # all loggers
                "handlers": handlers,
                "level": f"{log_level}",
                "propagate": False,
            }
        },
    }

    logging.config.dictConfig(logging_dict)

    # traceback logs configuration
    if TRACEBACK:
        global traceback_handler
        traceback_handler = logging.handlers.TimedRotatingFileHandler(
            f"{logs_dir}/wlsa-{command_name}-traceback.log",
            when=logging_dict["handlers"]["file"]["when"],
            interval=logging_dict["handlers"]["file"]["interval"],
            backupCount=logging_dict["handlers"]["file"]["backupCount"],
        )
        formatter = logging.Formatter(logging_dict["formatters"]["standard"]["format"])
        traceback_handler.setFormatter(formatter)


def get_logger(name):
    """
    Return a logger proxy that will forward the log messages to the logger with the provided name.
    """

    class LoggingProxy:
        def __init__(self, name):
            """
            Initialize the logger proxy. When the `TRACEBACK` global flag is set to `True`,
            the traceback logger is also initialized.
            """
            self.log = logging.getLogger(name)
            if TRACEBACK:
                self.traceback = traceback_manager.getLogger(name)
                self.traceback.addHandler(traceback_handler)

        def info(self, msg, *args, **kwargs):
            """
            Log 'msg % args' with severity 'INFO'. This method allows to use `console` option to print the message
            to the console along with the log file.
            """
            outstd = kwargs.pop("console", False)
            self.log.info(msg, *args, **kwargs)
            if outstd:
                print(msg)

        def warning(self, msg, *args, **kwargs):
            self.log.warning(msg, *args, **kwargs)

        def warn(self, msg, *args, **kwargs):
            self.log.warn(msg, *args, **kwargs)

        def error(self, msg, *args, **kwargs):
            """
            Log 'msg % args' with severity 'ERROR'. This method uses `TRACEBACK` global flag
            to print the traceback using the traceback logger.
            """
            self.log.error(msg, *args, **kwargs)
            if TRACEBACK:
                kwargs["exc_info"] = True
                self.traceback.error(msg, *args, **kwargs)

        def exception(self, msg, *args, exc_info=True, **kwargs):
            self.log.exception(msg, *args, exc_info=exc_info, **kwargs)

        def critical(self, msg, *args, **kwargs):
            self.log.critical(msg, *args, **kwargs)

        def fatal(self, msg, *args, **kwargs):
            self.log.fatal(msg, *args, **kwargs)

        def log(self, level, msg, *args, **kwargs):
            self.log.log(level, msg, *args, **kwargs)

        def debug(self, msg, *args, **kwargs):
            self.log.log(logging.DEBUG, msg, *args, **kwargs)

    return LoggingProxy(name)
