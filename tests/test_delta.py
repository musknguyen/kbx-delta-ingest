from main import plan_delta, estimate_chunks, MAX_TOKENS_PER_CHUNK


def test_new_updated_and_unchanged_articles():
    local = {"1": "h1", "2": "h2-new", "3": "h3"}
    remote = {
        "2": [{"name": "doc-2", "hash": "h2-old"}],
        "3": [{"name": "doc-3", "hash": "h3"}],
    }
    plan = plan_delta(local, remote, full_scrape=True)
    assert plan["add"] == ["1"]
    assert plan["update"] == ["2"]
    assert plan["skip"] == ["3"]
    assert plan["delete"] == []


def test_removed_articles_deleted_only_on_full_scrape():
    local = {"1": "h1"}
    remote = {"1": [{"name": "doc-1", "hash": "h1"}], "9": [{"name": "doc-9", "hash": "x"}]}
    assert plan_delta(local, remote, full_scrape=True)["delete"] == ["doc-9"]
    assert plan_delta(local, remote, full_scrape=False)["delete"] == []


def test_duplicate_documents_are_cleaned_up():
    local = {"1": "h1"}
    remote = {"1": [{"name": "doc-a", "hash": "h1"}, {"name": "doc-b", "hash": "h1"}]}
    plan = plan_delta(local, remote, full_scrape=False)
    assert plan["skip"] == ["1"]
    assert plan["delete"] == ["doc-b"]


def test_estimate_chunks():
    assert estimate_chunks("word " * 10) == 1
    assert estimate_chunks("word " * MAX_TOKENS_PER_CHUNK) == 1
    assert estimate_chunks("word " * (MAX_TOKENS_PER_CHUNK + 1)) == 2
    assert estimate_chunks("word " * 2000) == 5
