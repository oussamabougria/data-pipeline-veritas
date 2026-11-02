from __future__ import annotations

from veritas_core.models import DomainType, SourceType
from veritas_core.router import VeritasRouter


def test_csv_incident_columns_routes_to_batch_operational() -> None:
    source = VeritasRouter().route(
        connector="csv",
        columns=["incident_id", "severity", "status", "owner_team"],
        source_name="industrial_incidents.csv",
    )

    assert source.source_type == SourceType.BATCH
    assert source.domain_type == DomainType.OPERATIONAL
    assert VeritasRouter.branch(source) == "b"


def test_kafka_sensor_columns_routes_to_stream_sensor() -> None:
    source = VeritasRouter().route(
        connector="kafka",
        columns=["sensor_id", "temperature", "pressure", "rpm"],
        source_name="veritas.raw",
    )

    assert source.source_type == SourceType.STREAM
    assert source.domain_type == DomainType.SENSOR
    assert VeritasRouter.branch(source) == "a"


def test_csv_numeric_column_names_routes_to_batch_sensor() -> None:
    source = VeritasRouter().route(
        connector="csv",
        columns=["Time", "0", "1", "2", "3", "4", "5"],
        source_name="uci-secom.csv",
    )

    assert source.source_type == SourceType.BATCH
    assert source.domain_type == DomainType.SENSOR


def test_csv_retail_columns_routes_to_batch_operational() -> None:
    source = VeritasRouter().route(
        connector="csv",
        columns=["inventory", "units_sold", "price", "discount", "forecast"],
        source_name="retail_store_inventory.csv",
    )

    assert source.source_type == SourceType.BATCH
    assert source.domain_type == DomainType.OPERATIONAL


def test_mqtt_temperature_columns_routes_to_stream_sensor() -> None:
    source = VeritasRouter().route(
        connector="mqtt",
        columns=["temperature", "humidity", "voltage"],
        source_name="room_sensor_readings",
    )

    assert source.source_type == SourceType.STREAM
    assert source.domain_type == DomainType.SENSOR


def test_unknown_connector_unknown_columns_routes_to_batch_unknown() -> None:
    source = VeritasRouter().route(
        connector="spreadsheet",
        columns=["alpha", "beta", "gamma"],
        source_name="mystery",
    )

    assert source.source_type == SourceType.BATCH
    assert source.domain_type == DomainType.UNKNOWN
    assert VeritasRouter.branch(source) == "b"
