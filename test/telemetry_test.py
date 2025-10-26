from oban import telemetry


class TestTelemetryExecute:
    def test_handler_called_with_event_data(self):
        calls = []

        def handler(name, metadata):
            calls.append((name, metadata))

        telemetry.attach("test-handler", ["test.event"], handler)

        telemetry.execute("test.event", {"foo": "bar", "count": 42})

        [(name, metadata)] = calls
        assert name == "test.event"
        assert metadata == {"foo": "bar", "count": 42}

    def test_multiple_handlers(self):
        calls_1 = []
        calls_2 = []

        def handler_1(name, metadata):
            calls_1.append((name, metadata))

        def handler_2(name, metadata):
            calls_2.append((name, metadata))

        telemetry.attach("handler-1", ["test.event"], handler_1)
        telemetry.attach("handler-2", ["test.event"], handler_2)

        telemetry.execute("test.event", {"value": 123})

        [(_, metadata_1)] = calls_1
        [(_, metadata_2)] = calls_2
        assert metadata_1 == {"value": 123}
        assert metadata_2 == {"value": 123}

    def test_detach_removes_handler(self):
        calls = []

        def handler(name, metadata):
            calls.append((name, metadata))

        telemetry.attach("test-handler", ["test.event"], handler)
        telemetry.execute("test.event", {"before": True})

        telemetry.detach("test-handler")
        telemetry.execute("test.event", {"after": True})

        assert len(calls) == 1

    def test_handler_exception_does_not_break_execution(self):
        calls = []

        def broken_handler(name, metadata):
            raise ValueError("Handler error")

        def working_handler(name, metadata):
            calls.append((name, metadata))

        telemetry.attach("broken", ["test.event"], broken_handler)
        telemetry.attach("working", ["test.event"], working_handler)

        telemetry.execute("test.event", {"data": "test"})

        [(_, metadata)] = calls
        assert metadata == {"data": "test"}


class TestTelemetrySpan:
    def test_span_emits_start_and_stop_events(self):
        calls = []

        def handler(name, metadata):
            calls.append((name, metadata))

        telemetry.attach(
            "test-handler",
            ["test.operation.start", "test.operation.stop"],
            handler,
        )

        with telemetry.span("test.operation", {"job_id": 123}):
            pass

        [(start_name, start_meta), (stop_name, stop_meta)] = calls

        assert start_name == "test.operation.start"
        assert start_meta["job_id"] == 123
        assert "monotonic_time" in start_meta

        assert stop_name == "test.operation.stop"
        assert stop_meta["job_id"] == 123
        assert stop_meta["duration"] > 0

        telemetry.detach("test-handler")

    def test_span_includes_metadata_from_start_in_stop(self):
        calls = []

        def handler(name, metadata):
            calls.append((name, metadata))

        telemetry.attach("test-handler", ["test.operation.stop"], handler)

        with telemetry.span("test.operation", {"original": "data", "count": 42}):
            pass

        [(_, metadata)] = calls
        assert metadata["original"] == "data"
        assert metadata["count"] == 42

    def test_span_collector_adds_metadata_to_stop(self):
        calls = []

        def handler(name, metadata):
            calls.append((name, metadata))

        telemetry.attach("test-handler", ["test.operation.stop"], handler)

        with telemetry.span("test.operation", {"job_id": 123}) as collector:
            collector.add({"result": "success", "items": 5})

        [(_, metadata)] = calls
        assert metadata["job_id"] == 123
        assert metadata["result"] == "success"
        assert metadata["items"] == 5

    def test_span_emits_exception_event_on_error(self):
        calls = []

        def handler(name, metadata):
            calls.append((name, metadata))

        telemetry.attach("test-handler", ["test.operation.exception"], handler)

        try:
            with telemetry.span("test.operation", {"job_id": 456}):
                raise ValueError("Something went wrong")
        except ValueError:
            pass

        [(name, metadata)] = calls
        assert name == "test.operation.exception"
        assert metadata["job_id"] == 456
        assert metadata["error_type"] == "ValueError"
        assert metadata["error_message"] == "Something went wrong"
        assert "traceback" in metadata
        assert "duration" in metadata

    def test_span_reraises_exception(self):
        telemetry.attach(
            "test-handler", ["test.operation.exception"], lambda _name, _meta: None
        )

        with_raised = False
        try:
            with telemetry.span("test.operation", {}):
                raise ValueError("Test error")
        except ValueError:
            with_raised = True

        assert with_raised, "Exception should be re-raised"

        # Cleanup
        telemetry.detach("test-handler")
