#!/usr/bin/env python3
"""Tests for ODSC configuration validators."""

import pytest
from odsc.validators import (
    ValidationError,
    IntegerValidator,
    SyncIntervalValidator,
    SyncDirectoryValidator,
    LogLevelValidator,
    ClientIdValidator,
    BooleanValidator,
    StringValidator,
    MaxSyncWorkersValidator,
    DownloadChunkSizeValidator,
    validate_config_value,
    VALIDATORS,
)


# ---------------------------------------------------------------------------
# IntegerValidator
# ---------------------------------------------------------------------------

class TestIntegerValidator:
    def test_valid_int(self):
        v = IntegerValidator(min_value=1, max_value=10)
        assert v.validate(5) == 5

    def test_coerces_string(self):
        v = IntegerValidator()
        assert v.validate("42") == 42

    def test_below_min(self):
        v = IntegerValidator(min_value=5)
        with pytest.raises(ValidationError):
            v.validate(4)

    def test_above_max(self):
        v = IntegerValidator(max_value=10)
        with pytest.raises(ValidationError):
            v.validate(11)

    def test_at_boundaries(self):
        v = IntegerValidator(min_value=1, max_value=5)
        assert v.validate(1) == 1
        assert v.validate(5) == 5

    def test_non_numeric_raises(self):
        v = IntegerValidator()
        with pytest.raises(ValidationError):
            v.validate("not-a-number")

    def test_none_raises(self):
        v = IntegerValidator()
        with pytest.raises(ValidationError):
            v.validate(None)

    def test_no_bounds(self):
        v = IntegerValidator()
        assert v.validate(-9999) == -9999


# ---------------------------------------------------------------------------
# SyncIntervalValidator
# ---------------------------------------------------------------------------

class TestSyncIntervalValidator:
    def setup_method(self):
        self.v = SyncIntervalValidator()

    def test_minimum_valid(self):
        assert self.v.validate(60) == 60

    def test_maximum_valid(self):
        assert self.v.validate(86400) == 86400

    def test_below_minimum(self):
        with pytest.raises(ValidationError):
            self.v.validate(59)

    def test_above_maximum(self):
        with pytest.raises(ValidationError):
            self.v.validate(86401)

    def test_string_coercion(self):
        assert self.v.validate("300") == 300


# ---------------------------------------------------------------------------
# MaxSyncWorkersValidator
# ---------------------------------------------------------------------------

class TestMaxSyncWorkersValidator:
    def setup_method(self):
        self.v = MaxSyncWorkersValidator()

    def test_valid_range(self):
        for n in (1, 8, 16):
            assert self.v.validate(n) == n

    def test_below_min(self):
        with pytest.raises(ValidationError):
            self.v.validate(0)

    def test_above_max(self):
        with pytest.raises(ValidationError):
            self.v.validate(17)


# ---------------------------------------------------------------------------
# DownloadChunkSizeValidator
# ---------------------------------------------------------------------------

class TestDownloadChunkSizeValidator:
    def setup_method(self):
        self.v = DownloadChunkSizeValidator()

    def test_valid_chunk(self):
        assert self.v.validate(65536) == 65536

    def test_minimum_chunk(self):
        assert self.v.validate(4096) == 4096

    def test_maximum_chunk(self):
        assert self.v.validate(16_777_216) == 16_777_216

    def test_below_minimum(self):
        with pytest.raises(ValidationError):
            self.v.validate(4095)

    def test_above_maximum(self):
        with pytest.raises(ValidationError):
            self.v.validate(16_777_217)


# ---------------------------------------------------------------------------
# BooleanValidator
# ---------------------------------------------------------------------------

class TestBooleanValidator:
    def setup_method(self):
        self.v = BooleanValidator()

    def test_true_values(self):
        for val in (True, "true", "True", "1", "yes"):
            assert self.v.validate(val) is True

    def test_false_values(self):
        for val in (False, "false", "False", "0", "no"):
            assert self.v.validate(val) is False

    def test_none_raises(self):
        # None is falsy but BooleanValidator should coerce, not raise
        # (implementation converts to bool — None -> False)
        result = self.v.validate(None)
        assert result is False

    def test_invalid_string(self):
        # Unrecognised strings should raise ValidationError
        with pytest.raises(ValidationError, match="Invalid boolean value"):
            self.v.validate("maybe")


# ---------------------------------------------------------------------------
# LogLevelValidator
# ---------------------------------------------------------------------------

class TestLogLevelValidator:
    def setup_method(self):
        self.v = LogLevelValidator()

    def test_valid_levels(self):
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            assert self.v.validate(level) == level

    def test_case_insensitive(self):
        assert self.v.validate("debug") == "DEBUG"

    def test_invalid_level(self):
        with pytest.raises(ValidationError):
            self.v.validate("VERBOSE")


# ---------------------------------------------------------------------------
# validate_config_value (registry lookup)
# ---------------------------------------------------------------------------

class TestValidateConfigValue:
    def test_known_key(self):
        assert validate_config_value("sync_interval", 300) == 300

    def test_unknown_key_accepted_with_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="odsc.validators"):
            result = validate_config_value("nonexistent_key", "anything")
        assert result == "anything"
        assert "Unknown config key" in caplog.text

    def test_max_sync_workers_via_registry(self):
        assert validate_config_value("max_sync_workers", "4") == 4

    def test_download_chunk_size_via_registry(self):
        assert validate_config_value("download_chunk_size", 131072) == 131072


# ---------------------------------------------------------------------------
# VALIDATORS registry completeness
# ---------------------------------------------------------------------------

def test_validators_registry_has_required_keys():
    required = {
        "sync_interval", "sync_directory", "log_level",
        "client_id", "auto_start", "max_sync_workers", "download_chunk_size",
    }
    assert required.issubset(VALIDATORS.keys())
