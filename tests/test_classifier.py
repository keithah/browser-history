from browser_history import classifier


def test_classify_github_repo_pull_request():
    primary, labels, summary = classifier.classify("https://github.com/owner/repo/pull/12", "")
    assert primary == "github_project"
    assert "github_project" in labels
    assert "owner/repo" in summary
    assert "PR #12" in summary


def test_classify_ai_and_personal_sites():
    primary_ai, labels_ai, _ = classifier.classify("https://openai.com/research", "OpenAI")
    assert primary_ai == "ai"
    assert "ai" in labels_ai

    primary_personal, labels_personal, _ = classifier.classify("https://blog.example.com/posts/1", "My blog post")
    assert primary_personal == "personal_site"
    assert "personal_site" in labels_personal
