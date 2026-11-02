"""
veritas_simulator.py
=====================
Industrial sensor simulator for Veritas platform stress testing.

PURPOSE: Find problems before production does.
- High volume bursts to stress Kafka + Flink ingestion
- Malformed / edge case messages to test validation
- Anomaly injection to trigger quarantine pipeline
- Multiple sensors at realistic frequencies simultaneously
- Memory leak detection via long-run mode
- Metrics printed every 10 seconds so you can watch the system react

USAGE:
    # Normal stress test (recommended first run)
    python veritas_simulator.py

    # Nuclear mode - maximum load
    python veritas_simulator.py --mode nuclear

    # Anomaly flood - trigger as many quarantine cards as possible
    python veritas_simulator.py --mode anomaly

    # Long run - 30 minutes, watch for memory/lag issues
    python veritas_simulator.py --mode longrun

    # Chaos mode - random everything including malformed messages
    python veritas_simulator.py --mode chaos

REQUIREMENTS:
    pip install kafka-python rich
"""

import argparse
import json
import math
import random
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

try:
    from kafka import KafkaProducer
    from kafka.errors import KafkaError, NoBrokersAvailable
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
except ImportError:
    print("Missing dependencies. Run: pip install kafka-python rich")
    sys.exit(1)


# ── Configuration ─────────────────────────────────────────────────────────────

KAFKA_BOOTSTRAP = "localhost:29092"
TOPIC           = "veritas.raw"

# Sensor definitions — realistic physical envelopes
SENSORS = {
    "TEMP-AUTOCLAVE-1": {
        "unit": "C",
        "archetype": "INERTIAL",
        "hz": 1.0,
        "normal_range": (150.0, 210.0),
        "anomaly_range": (0.0, 9999.0),
        "description": "Autoclave cure temperature",
        "cycle": "thermal",          # simulates cure cycle ramp/hold/cool
    },
    "VIBR-SPINDLE-1": {
        "unit": "mm/s",
        "archetype": "CYCLICAL",
        "hz": 10.0,                  # burst simulate 100Hz
        "normal_range": (0.5, 8.0),
        "anomaly_range": (0.0, 150.0),
        "description": "Spindle vibration RMS",
        "cycle": "vibration",
    },
    "WEAR-TOOL-1": {
        "unit": "um",
        "archetype": "CUMULATIVE",
        "hz": 0.2,
        "normal_range": (0.0, 120.0),
        "anomaly_range": (400.0, 600.0),
        "description": "Tool wear indicator",
        "cycle": "wear",             # monotonically increases
    },
    "VALVE-COOLANT-1": {
        "unit": "bool",
        "archetype": "BINARY",
        "hz": 2.0,
        "normal_range": (0.0, 1.0),
        "anomaly_range": (-1.0, 2.0),  # invalid binary values
        "description": "Coolant valve state",
        "cycle": "binary",
    },
    "PRESS-HYDRAULIC-1": {
        "unit": "bar",
        "archetype": "INERTIAL",
        "hz": 5.0,
        "normal_range": (80.0, 120.0),
        "anomaly_range": (0.0, 400.0),
        "description": "Hydraulic press pressure",
        "cycle": "pressure",
    },
    "TEMP-COOLANT-1": {
        "unit": "C",
        "archetype": "INERTIAL",
        "hz": 1.0,
        "normal_range": (15.0, 35.0),
        "anomaly_range": (0.0, 150.0),
        "description": "Coolant fluid temperature",
        "cycle": "thermal",
    },
}

# ── Metrics ───────────────────────────────────────────────────────────────────

@dataclass
class SimMetrics:
    sent_total:       int = 0
    sent_anomaly:     int = 0
    sent_malformed:   int = 0
    sent_edge:        int = 0
    errors:           int = 0
    kafka_errors:     int = 0
    start_time:       float = field(default_factory=time.time)
    last_report_time: float = field(default_factory=time.time)
    lock:             threading.Lock = field(default_factory=threading.Lock)

    def rate(self) -> float:
        elapsed = time.time() - self.start_time
        return self.sent_total / elapsed if elapsed > 0 else 0.0

    def elapsed_str(self) -> str:
        e = int(time.time() - self.start_time)
        return f"{e//3600:02d}:{(e%3600)//60:02d}:{e%60:02d}"


METRICS = SimMetrics()
RUNNING = True


# ── Signal cycle simulators ───────────────────────────────────────────────────

class SignalGenerator:
    """Generates realistic time-varying sensor values."""

    def __init__(self):
        self._t = 0.0
        self._wear_accumulator = 0.0
        self._valve_state = 1.0
        self._valve_counter = 0
        self._thermal_phase = 0.0   # 0=ramp, 1=hold, 2=cool

    def next(self, sensor_id: str, sensor_cfg: dict) -> float:
        self._t += 1.0 / sensor_cfg["hz"]
        cycle = sensor_cfg["cycle"]
        lo, hi = sensor_cfg["normal_range"]

        if cycle == "thermal":
            # Simulate autoclave cure cycle: ramp 20min → hold 60min → cool 20min
            phase = (self._t % 3600) / 3600
            if phase < 0.33:      # ramp
                base = lo + (hi - lo) * (phase / 0.33)
            elif phase < 0.83:    # hold
                base = hi
            else:                  # cool
                base = hi - (hi - lo) * ((phase - 0.83) / 0.17)
            return base + random.gauss(0, 0.8)

        elif cycle == "vibration":
            # Sinusoidal with harmonic noise
            base = (lo + hi) / 2
            amplitude = (hi - lo) / 4
            return max(lo, base + amplitude * math.sin(self._t * 2.3)
                       + random.gauss(0, 0.15))

        elif cycle == "wear":
            # Monotonically increasing with occasional step changes
            self._wear_accumulator += random.uniform(0.001, 0.05)
            return min(hi, lo + self._wear_accumulator)

        elif cycle == "binary":
            # Toggle every ~30 seconds
            self._valve_counter += 1
            if self._valve_counter > int(30 * sensor_cfg["hz"]):
                self._valve_state = 1.0 - self._valve_state
                self._valve_counter = 0
            return self._valve_state

        elif cycle == "pressure":
            # Mean-reverting pressure with spikes
            base = (lo + hi) / 2
            noise = random.gauss(0, 3.0)
            return max(lo * 0.8, min(hi * 1.1, base + noise))

        return random.uniform(lo, hi)


SIGNAL_GEN = SignalGenerator()


# ── Message builders ──────────────────────────────────────────────────────────

def build_normal(sensor_id: str, cfg: dict) -> dict:
    return {
        "sensor_id":   sensor_id,
        "value":       round(SIGNAL_GEN.next(sensor_id, cfg), 4),
        "unit":        cfg["unit"],
        "archetype":   cfg["archetype"],
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source":      "simulator",
    }


def build_anomaly(sensor_id: str, cfg: dict) -> dict:
    """Inject a physically implausible value."""
    anomaly_types = [
        # Spike
        lambda: random.uniform(*cfg["anomaly_range"]),
        # Flatline (exactly the same value repeatedly)
        lambda: cfg["normal_range"][0],
        # Negative (always wrong for physical quantities)
        lambda: -abs(random.uniform(1, 100)),
        # Near-zero on a sensor that should never be zero
        lambda: random.uniform(0.0001, 0.001),
        # Extreme max
        lambda: cfg["anomaly_range"][1],
    ]
    return {
        "sensor_id":    sensor_id,
        "value":        round(random.choice(anomaly_types)(), 4),
        "unit":         cfg["unit"],
        "archetype":    cfg["archetype"],
        "observed_at":  datetime.now(timezone.utc).isoformat(),
        "source":       "simulator",
        "_injected":    "ANOMALY",
    }


def build_malformed() -> bytes:
    """Messages that should fail schema validation."""
    malformed_types = [
        b"not json at all",
        b"",
        b"null",
        b'{"sensor_id": null, "value": "not_a_number"}',
        b'{"sensor_id": "TEMP-AUTOCLAVE-1"}',          # missing value
        b'{"value": 42.0}',                             # missing sensor_id
        b'{"sensor_id": "", "value": 42.0}',            # empty sensor_id
        b'{"sensor_id": "TEMP-AUTOCLAVE-1", "value": null}',
        b'{"sensor_id": "TEMP-AUTOCLAVE-1", "value": Infinity}',
        json.dumps({"sensor_id": "X" * 10000, "value": 42.0}).encode(),  # giant key
        b'[1, 2, 3]',                                   # array not object
        b'{"sensor_id": "TEMP-AUTOCLAVE-1", "value": 1e999}',  # overflow float
    ]
    return random.choice(malformed_types)


def build_edge_case(sensor_id: str, cfg: dict) -> dict:
    """Valid JSON but edge case values that stress the scoring logic."""
    edge_types = [
        # Exactly at boundary
        {"value": cfg["normal_range"][0]},
        {"value": cfg["normal_range"][1]},
        # Zero
        {"value": 0.0},
        # Very high precision
        {"value": 123.456789012345},
        # Far future timestamp
        {"observed_at": "2099-12-31T23:59:59Z"},
        # Far past timestamp
        {"observed_at": "1970-01-01T00:00:00Z"},
        # Missing timestamp (should default)
        {},
        # Duplicate send (same timestamp twice)
        {"observed_at": "2026-04-25T00:00:00.000000Z"},
        # Unicode in unit
        {"unit": "°C±0.1"},
        # Extra unknown fields
        {"_extra_field": "should_be_ignored", "_another": 42},
    ]
    base = build_normal(sensor_id, cfg)
    base.update(random.choice(edge_types))
    base["_injected"] = "EDGE_CASE"
    return base


# ── Burst sender ──────────────────────────────────────────────────────────────

def burst_send(producer: KafkaProducer, n: int, sensor_id: str, cfg: dict):
    """Send n messages as fast as possible — stress test Kafka throughput."""
    for _ in range(n):
        msg = build_normal(sensor_id, cfg)
        try:
            producer.send(TOPIC, value=json.dumps(msg).encode())
            with METRICS.lock:
                METRICS.sent_total += 1
        except KafkaError as e:
            with METRICS.lock:
                METRICS.kafka_errors += 1


# ── Sensor thread ─────────────────────────────────────────────────────────────

def sensor_thread(
    sensor_id:      str,
    cfg:            dict,
    producer:       KafkaProducer,
    anomaly_rate:   float,
    malformed_rate: float,
    edge_rate:      float,
    burst_mode:     bool,
):
    """
    One thread per sensor. Runs at sensor's natural frequency.
    Randomly injects anomalies, malformed messages, and edge cases.
    """
    interval = 1.0 / cfg["hz"]

    while RUNNING:
        loop_start = time.time()

        try:
            roll = random.random()

            if roll < malformed_rate:
                # Send raw bytes — bypass JSON encoding
                raw = build_malformed()
                producer.send(TOPIC, value=raw)
                with METRICS.lock:
                    METRICS.sent_total += 1
                    METRICS.sent_malformed += 1

            elif roll < malformed_rate + edge_rate:
                msg = build_edge_case(sensor_id, cfg)
                producer.send(TOPIC, value=json.dumps(msg).encode())
                with METRICS.lock:
                    METRICS.sent_total += 1
                    METRICS.sent_edge += 1

            elif roll < malformed_rate + edge_rate + anomaly_rate:
                msg = build_anomaly(sensor_id, cfg)
                producer.send(TOPIC, value=json.dumps(msg).encode())
                with METRICS.lock:
                    METRICS.sent_total += 1
                    METRICS.sent_anomaly += 1

            else:
                msg = build_normal(sensor_id, cfg)
                producer.send(TOPIC, value=json.dumps(msg).encode())
                with METRICS.lock:
                    METRICS.sent_total += 1

            # Burst mode: occasionally flood 500 messages at once
            if burst_mode and random.random() < 0.02:
                burst_send(producer, 500, sensor_id, cfg)

        except Exception as e:
            with METRICS.lock:
                METRICS.errors += 1

        # Sleep for remaining interval
        elapsed = time.time() - loop_start
        sleep_time = max(0, interval - elapsed)
        time.sleep(sleep_time)


# ── Metrics display ───────────────────────────────────────────────────────────

def build_metrics_table(mode: str) -> Table:
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    t.add_column("key", style="cyan")
    t.add_column("value", style="bright_white")

    t.add_row("Mode",          f"[bold yellow]{mode.upper()}[/]")
    t.add_row("Elapsed",       METRICS.elapsed_str())
    t.add_row("Total sent",    f"[green]{METRICS.sent_total:,}[/]")
    t.add_row("  Normal",      f"{METRICS.sent_total - METRICS.sent_anomaly - METRICS.sent_malformed - METRICS.sent_edge:,}")
    t.add_row("  Anomalies",   f"[red]{METRICS.sent_anomaly:,}[/]")
    t.add_row("  Malformed",   f"[yellow]{METRICS.sent_malformed:,}[/]")
    t.add_row("  Edge cases",  f"[magenta]{METRICS.sent_edge:,}[/]")
    t.add_row("Msg/sec",       f"[bold]{METRICS.rate():.1f}[/]")
    t.add_row("Errors",        f"[red]{METRICS.errors + METRICS.kafka_errors:,}[/]" if METRICS.errors + METRICS.kafka_errors > 0 else "0")
    t.add_row("Kafka errors",  f"[red]{METRICS.kafka_errors:,}[/]" if METRICS.kafka_errors > 0 else "0")

    return t


# ── Mode configurations ───────────────────────────────────────────────────────

MODES = {
    "normal": {
        "anomaly_rate":   0.12,   # 1 in 8
        "malformed_rate": 0.02,   # 1 in 50
        "edge_rate":      0.05,   # 1 in 20
        "burst_mode":     False,
        "description":    "Realistic production load with occasional anomalies",
    },
    "nuclear": {
        "anomaly_rate":   0.20,
        "malformed_rate": 0.05,
        "edge_rate":      0.10,
        "burst_mode":     True,   # 500-message bursts randomly
        "description":    "Maximum load — bursts + high anomaly rate. Will stress RAM and Kafka lag.",
    },
    "anomaly": {
        "anomaly_rate":   0.60,   # 60% anomalies
        "malformed_rate": 0.01,
        "edge_rate":      0.05,
        "burst_mode":     False,
        "description":    "Anomaly flood — hammers quarantine pipeline and LLM enrichment queue.",
    },
    "chaos": {
        "anomaly_rate":   0.25,
        "malformed_rate": 0.25,   # 25% malformed — schema validation stress test
        "edge_rate":      0.25,
        "burst_mode":     True,
        "description":    "Chaos mode — everything breaks at once. Find your weakest point.",
    },
    "longrun": {
        "anomaly_rate":   0.08,
        "malformed_rate": 0.01,
        "edge_rate":      0.02,
        "burst_mode":     False,
        "description":    "Low-intensity long run — watch for memory leaks and Kafka consumer lag.",
    },
}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global RUNNING

    parser = argparse.ArgumentParser(description="Veritas Platform Stress Simulator")
    parser.add_argument(
        "--mode",
        choices=list(MODES.keys()),
        default="normal",
        help="Simulation mode (default: normal)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=0,
        help="Run for N seconds then stop (0 = run until Ctrl-C)",
    )
    args = parser.parse_args()

    mode_cfg = MODES[args.mode]
    console = Console()

    console.print(f"\n[bold cyan]VERITAS SIMULATOR[/] — [yellow]{args.mode.upper()}[/]")
    console.print(f"[dim]{mode_cfg['description']}[/]\n")
    console.print(f"Connecting to Kafka at [cyan]{KAFKA_BOOTSTRAP}[/] → topic [cyan]{TOPIC}[/]\n")

    # Connect to Kafka
    try:
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            acks=1,                          # don't wait for full replication — speed
            retries=3,
            linger_ms=5,                     # batch for 5ms — throughput vs latency tradeoff
            batch_size=65536,                # 64KB batches
            compression_type="gzip",
            max_block_ms=5000,
        )
        console.print("[green]✓ Kafka connected[/]\n")
    except NoBrokersAvailable:
        console.print(f"[red]✗ Cannot reach Kafka at {KAFKA_BOOTSTRAP}[/]")
        console.print("[dim]Is the stack running? Try: docker compose ps[/]")
        sys.exit(1)

    # Graceful shutdown
    def shutdown(sig, frame):
        global RUNNING
        RUNNING = False
        console.print("\n[yellow]Shutting down...[/]")

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start one thread per sensor
    threads = []
    for sensor_id, cfg in SENSORS.items():
        t = threading.Thread(
            target=sensor_thread,
            args=(
                sensor_id,
                cfg,
                producer,
                mode_cfg["anomaly_rate"],
                mode_cfg["malformed_rate"],
                mode_cfg["edge_rate"],
                mode_cfg["burst_mode"],
            ),
            daemon=True,
            name=f"sim-{sensor_id}",
        )
        t.start()
        threads.append(t)
        console.print(f"[green]→[/] Started sensor thread: [cyan]{sensor_id}[/] @ {cfg['hz']}Hz")

    console.print(f"\n[bold]Running {len(threads)} sensor threads. Press Ctrl-C to stop.[/]\n")

    # Duration timer
    start = time.time()

    # Live metrics display
    with Live(console=console, refresh_per_second=1) as live:
        while RUNNING:
            if args.duration > 0 and (time.time() - start) >= args.duration:
                RUNNING = False
                break

            panel = Panel(
                build_metrics_table(args.mode),
                title="[bold cyan]VERITAS SIMULATOR — LIVE METRICS[/]",
                border_style="cyan",
            )
            live.update(panel)
            time.sleep(1)

    # Flush remaining messages
    console.print("\n[dim]Flushing Kafka producer...[/]")
    producer.flush(timeout=10)
    producer.close()

    # Final report
    console.print("\n[bold]═══ FINAL REPORT ═══[/]")
    console.print(f"Total messages sent : [green]{METRICS.sent_total:,}[/]")
    console.print(f"Anomalies injected  : [red]{METRICS.sent_anomaly:,}[/]")
    console.print(f"Malformed sent      : [yellow]{METRICS.sent_malformed:,}[/]")
    console.print(f"Edge cases sent     : [magenta]{METRICS.sent_edge:,}[/]")
    console.print(f"Average rate        : [bold]{METRICS.rate():.1f} msg/sec[/]")
    console.print(f"Total errors        : [red]{METRICS.errors + METRICS.kafka_errors:,}[/]")
    console.print(f"Duration            : {METRICS.elapsed_str()}")

    console.print("\n[dim]Now check your pipeline:[/]")
    console.print('[dim]docker exec veritas_postgres psql -U postgres -d veritas -c "SELECT COUNT(*), AVG(confidence), SUM(CASE WHEN is_anomaly THEN 1 ELSE 0 END) AS anomalies FROM veritas.scored_observations;"[/]')


if __name__ == "__main__":
    main()
