class OperationalError(Exception):
    pass


class DataLoadError(OperationalError):
    pass


class ProfilingError(OperationalError):
    pass


class RulesEngineError(OperationalError):
    pass
