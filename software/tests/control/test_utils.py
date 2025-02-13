import control.utils


def test_squid_repo_info():
    # At least make sure we get something and that it calls without issue.
    assert control.utils.get_squid_repo_state_description()
