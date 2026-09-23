from yowayowa.acquisition.auth import AuthSignal, HeuristicAuthDetector


def _detector() -> HeuristicAuthDetector:
    return HeuristicAuthDetector()


def test_401_status_means_unauthenticated() -> None:
    state = _detector().detect(AuthSignal(url="https://broker.example/account", status_code=401))
    assert state.value == "unauthenticated"


def test_403_status_means_unauthenticated() -> None:
    state = _detector().detect(AuthSignal(url="https://broker.example/account", status_code=403))
    assert state.value == "unauthenticated"


def test_login_marker_in_url_path_means_unauthenticated() -> None:
    state = _detector().detect(AuthSignal(url="https://broker.example/auth/login", status_code=200))
    assert state.value == "unauthenticated"


def test_login_text_marker_in_small_body_means_unauthenticated() -> None:
    state = _detector().detect(
        AuthSignal(
            url="https://broker.example/top",
            status_code=200,
            title="Member Login",
            body_text="<html><body>Log in to continue</body></html>",
        )
    )
    assert state.value == "unauthenticated"


def test_big_body_with_login_marker_is_not_treated_as_login_page() -> None:
    big_body = "x" * 200_000 + "Log in"
    state = _detector().detect(
        AuthSignal(url="https://broker.example/top", status_code=200, body_text=big_body)
    )
    assert state.value == "authenticated"


def test_empty_signal_is_unknown() -> None:
    assert _detector().detect(AuthSignal()).value == "unknown"
    # A 5xx status proves neither auth state; callers must treat UNKNOWN
    # as not-authenticated and block.
    assert _detector().detect(AuthSignal(url="https://x/pos", status_code=500)).value == "unknown"


def test_200_response_is_authenticated() -> None:
    state = _detector().detect(
        AuthSignal(url="https://broker.example/positions", status_code=200, body_text="[]")
    )
    assert state.value == "authenticated"


def test_3xx_response_is_authenticated() -> None:
    state = _detector().detect(AuthSignal(url="https://broker.example/positions", status_code=302))
    assert state.value == "authenticated"


def test_japanese_login_marker_detected() -> None:
    state = _detector().detect(
        AuthSignal(url="https://broker.example/top", status_code=200, body_text="ログイン")
    )
    assert state.value == "unauthenticated"


def test_url_login_marker_only_matches_path_not_query() -> None:
    # Query strings are stripped from provenance urls; markers in a hostname or
    # a data query value must not trip detection when the path is clean.
    state = _detector().detect(
        AuthSignal(url="https://broker.example/positions?next=/login", status_code=200)
    )
    assert state.value == "authenticated"


def test_default_url_markers_drop_auth_marker() -> None:
    # Rakuten's legitimate URLs contain "auth"; the "auth" marker misclassified
    # them as login pages, so the shipped defaults are login/signin/sign-in only.
    assert _detector().login_url_markers == ("login", "signin", "sign-in")
    state = _detector().detect(AuthSignal(url="https://broker.example/auth/api", status_code=200))
    assert state.value == "authenticated"


def test_strip_url_query_variants() -> None:
    from yowayowa.acquisition.auth import strip_url_query

    assert strip_url_query("https://x.example/a?b=1") == "https://x.example/a"
    assert strip_url_query("https://x.example/a#frag") == "https://x.example/a"
    assert strip_url_query("https://x.example/a?b=1#frag") == "https://x.example/a"
    assert strip_url_query("https://x.example/a") == "https://x.example/a"
    assert strip_url_query("") == ""
