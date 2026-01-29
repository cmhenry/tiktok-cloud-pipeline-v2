#!/usr/bin/env python3
"""
Connectivity Validation Tests

Tests network connectivity to all required services:
- Redis (queue management)
- PostgreSQL (metadata storage)
- S3 (archive and processed file storage)
- GPU (NVIDIA driver and CUDA)

Usage:
    python -m tests.test_connectivity
    python -m tests.test_connectivity --service redis
    python -m tests.test_connectivity --verbose
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import REDIS, POSTGRES, S3, _ENV_FILE_LOADED


class ConnectivityTester:
    """Test connectivity to pipeline services."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.results = {}

    def log(self, msg: str, level: str = "INFO"):
        """Print log message."""
        prefix = {"INFO": "[*]", "OK": "[+]", "FAIL": "[-]", "WARN": "[!]"}
        print(f"{prefix.get(level, '[*]')} {msg}")

    def _print_hints(self, service: str, error_str: str):
        """Print targeted troubleshooting hints based on the error message."""
        err = error_str.lower()
        if "connection refused" in err:
            self.log(
                f"{service} may not be listening on the network interface. "
                "Run `sudo ./deploy/diagnose-services.sh` on the coordinator.",
                "WARN",
            )
        if "pg_hba.conf" in err:
            self.log(
                "PostgreSQL is rejecting this worker's IP. Check pg_hba.conf on "
                "the coordinator or re-run `sudo ./deploy/setup-coordinator.sh`.",
                "WARN",
            )
        if "timeout" in err or "timed out" in err:
            self.log(
                f"Firewall or routing issue reaching {service}. Verify the "
                "coordinator IP and that the port is open.",
                "WARN",
            )

    def print_config(self):
        """Print resolved configuration so the user can spot misconfigurations."""
        def _mask(value):
            if not value:
                return "(not set)"
            if len(value) <= 4:
                return "****"
            return value[:2] + "****" + value[-2:]

        self.log("=" * 60)
        self.log("Resolved Configuration")
        self.log("=" * 60)
        self.log(f".env file: {_ENV_FILE_LOADED or 'none found (using defaults)'}")
        self.log(f"Redis:     {REDIS['HOST']}:{REDIS['PORT']}")
        self.log(
            f"Postgres:  {POSTGRES['HOST']}:{POSTGRES['PORT']}  "
            f"db={POSTGRES['DATABASE']}  user={POSTGRES['USER']}  "
            f"password={_mask(POSTGRES['PASSWORD'])}"
        )
        self.log(
            f"S3:        endpoint={S3['ENDPOINT'] or '(not set)'}  "
            f"bucket={S3['BUCKET']}  "
            f"access_key={_mask(S3['ACCESS_KEY'] or '')}  "
            f"secret_key={_mask(S3['SECRET_KEY'] or '')}"
        )
        print()

    def test_redis(self) -> bool:
        """
        Test Redis connectivity.

        Verifies:
        - TCP connection to Redis host:port
        - PING command response
        - Queue access (read/write)
        """
        self.log("Testing Redis connectivity...")

        try:
            import redis

            client = redis.Redis(
                host=REDIS["HOST"],
                port=REDIS["PORT"],
                decode_responses=True,
                socket_timeout=5,
            )

            # Test PING
            response = client.ping()
            if not response:
                self.log("Redis PING failed", "FAIL")
                return False
            self.log(f"Redis PING: OK (host={REDIS['HOST']}:{REDIS['PORT']})", "OK")

            # Test queue operations
            test_key = "_connectivity_test"
            client.lpush(test_key, "test")
            value = client.rpop(test_key)
            if value != "test":
                self.log("Redis queue operations failed", "FAIL")
                return False
            self.log("Redis queue operations: OK", "OK")

            # Show queue depths
            if self.verbose:
                for name, queue in REDIS["QUEUES"].items():
                    depth = client.llen(queue)
                    self.log(f"  Queue {queue}: {depth} items")

            self.results["redis"] = True
            return True

        except redis.ConnectionError as e:
            self.log(f"Redis connection failed: {e}", "FAIL")
            self._print_hints("Redis", str(e))
            self.results["redis"] = False
            return False
        except Exception as e:
            self.log(f"Redis test error: {e}", "FAIL")
            self._print_hints("Redis", str(e))
            self.results["redis"] = False
            return False

    def test_postgres(self) -> bool:
        """
        Test PostgreSQL connectivity.

        Verifies:
        - TCP connection to PostgreSQL host:port
        - Authentication with configured credentials
        - Database access
        - Table existence (audio_files, pipeline_transcripts, pipeline_classifications)
        """
        self.log("Testing PostgreSQL connectivity...")

        try:
            import psycopg2

            conn = psycopg2.connect(
                host=POSTGRES["HOST"],
                port=POSTGRES["PORT"],
                dbname=POSTGRES["DATABASE"],
                user=POSTGRES["USER"],
                password=POSTGRES["PASSWORD"],
                connect_timeout=5,
            )

            self.log(
                f"PostgreSQL connection: OK "
                f"(host={POSTGRES['HOST']}:{POSTGRES['PORT']}, db={POSTGRES['DATABASE']})",
                "OK",
            )

            with conn.cursor() as cur:
                # Test basic query
                cur.execute("SELECT 1")
                result = cur.fetchone()
                if result[0] != 1:
                    self.log("PostgreSQL query test failed", "FAIL")
                    return False
                self.log("PostgreSQL query: OK", "OK")

                # Check required tables exist
                required_tables = [
                    "audio_files",
                    "pipeline_transcripts",
                    "pipeline_classifications",
                ]

                cur.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    """
                )
                existing_tables = {row[0] for row in cur.fetchall()}

                missing = set(required_tables) - existing_tables
                if missing:
                    self.log(f"Missing tables: {missing}", "FAIL")
                    return False
                self.log(f"Required tables exist: {required_tables}", "OK")

                # Show table stats if verbose
                if self.verbose:
                    for table in required_tables:
                        cur.execute(f"SELECT COUNT(*) FROM {table}")
                        count = cur.fetchone()[0]
                        self.log(f"  {table}: {count} rows")

            conn.close()
            self.results["postgres"] = True
            return True

        except psycopg2.OperationalError as e:
            self.log(f"PostgreSQL connection failed: {e}", "FAIL")
            self._print_hints("PostgreSQL", str(e))
            self.results["postgres"] = False
            return False
        except Exception as e:
            self.log(f"PostgreSQL test error: {e}", "FAIL")
            self._print_hints("PostgreSQL", str(e))
            self.results["postgres"] = False
            return False

    def test_s3(self) -> bool:
        """
        Test S3 connectivity.

        Verifies:
        - Endpoint reachability
        - Credential validity
        - Bucket access
        - Read/write permissions
        """
        self.log("Testing S3 connectivity...")

        # Check required config
        if not S3["ENDPOINT"]:
            self.log("S3_ENDPOINT not configured", "FAIL")
            self.results["s3"] = False
            return False

        if not S3["ACCESS_KEY"] or not S3["SECRET_KEY"]:
            self.log("S3 credentials not configured", "FAIL")
            self.results["s3"] = False
            return False

        try:
            from src.s3_utils import check_s3_connection, get_s3_client

            # Use existing check function
            if not check_s3_connection():
                self.results["s3"] = False
                return False

            self.log(f"S3 bucket accessible: {S3['BUCKET']} at {S3['ENDPOINT']}", "OK")

            # Test write permission with a small test object
            if self.verbose:
                client = get_s3_client()
                test_key = "_connectivity_test"
                try:
                    client.put_object(
                        Bucket=S3["BUCKET"], Key=test_key, Body=b"test"
                    )
                    self.log("S3 write permission: OK", "OK")

                    # Cleanup test object
                    client.delete_object(Bucket=S3["BUCKET"], Key=test_key)
                except Exception as e:
                    self.log(f"S3 write test failed: {e}", "WARN")

            self.results["s3"] = True
            return True

        except Exception as e:
            self.log(f"S3 test error: {e}", "FAIL")
            self.results["s3"] = False
            return False

    def test_gpu(self) -> bool:
        """
        Test GPU availability.

        Verifies:
        - NVIDIA driver installed
        - GPU detected via nvidia-smi
        - CUDA available via PyTorch (if installed)
        """
        self.log("Testing GPU availability...")

        # Test nvidia-smi
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                self.log("nvidia-smi failed", "FAIL")
                self.results["gpu"] = False
                return False

            gpu_info = result.stdout.strip()
            self.log(f"GPU detected: {gpu_info}", "OK")

        except FileNotFoundError:
            self.log("nvidia-smi not found - NVIDIA driver not installed", "FAIL")
            self.results["gpu"] = False
            return False
        except subprocess.TimeoutExpired:
            self.log("nvidia-smi timed out", "FAIL")
            self.results["gpu"] = False
            return False

        # Test CUDA via PyTorch
        try:
            import torch

            if torch.cuda.is_available():
                device_name = torch.cuda.get_device_name(0)
                memory_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                self.log(f"PyTorch CUDA: OK ({device_name}, {memory_gb:.1f}GB)", "OK")

                if self.verbose:
                    self.log(f"  CUDA version: {torch.version.cuda}")
                    self.log(f"  PyTorch version: {torch.__version__}")
            else:
                self.log("PyTorch CUDA not available", "WARN")

        except ImportError:
            self.log("PyTorch not installed - skipping CUDA test", "WARN")

        self.results["gpu"] = True
        return True

    def run_all(self) -> bool:
        """Run all connectivity tests."""
        self.print_config()
        self.log("=" * 60)
        self.log("Pipeline Connectivity Tests")
        self.log("=" * 60)
        print()

        tests = [
            ("Redis", self.test_redis),
            ("PostgreSQL", self.test_postgres),
            ("S3", self.test_s3),
            ("GPU", self.test_gpu),
        ]

        all_passed = True
        for name, test_func in tests:
            try:
                passed = test_func()
                if not passed:
                    all_passed = False
            except Exception as e:
                self.log(f"{name} test crashed: {e}", "FAIL")
                all_passed = False
            print()

        # Summary
        self.log("=" * 60)
        self.log("Summary")
        self.log("=" * 60)
        for service, passed in self.results.items():
            status = "PASS" if passed else "FAIL"
            level = "OK" if passed else "FAIL"
            self.log(f"{service.upper()}: {status}", level)

        return all_passed


def main():
    parser = argparse.ArgumentParser(
        description="Test connectivity to pipeline services"
    )
    parser.add_argument(
        "--service",
        choices=["redis", "postgres", "s3", "gpu", "all"],
        default="all",
        help="Service to test (default: all)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output",
    )

    args = parser.parse_args()

    tester = ConnectivityTester(verbose=args.verbose)

    if args.service == "all":
        success = tester.run_all()
    else:
        tester.print_config()
        test_map = {
            "redis": tester.test_redis,
            "postgres": tester.test_postgres,
            "s3": tester.test_s3,
            "gpu": tester.test_gpu,
        }
        success = test_map[args.service]()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
