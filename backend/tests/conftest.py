def pytest_sessionfinish(session, exitstatus) -> None:
    del session, exitstatus
    from api_client import cleanup_authenticated_test_client_state

    cleanup_authenticated_test_client_state()
