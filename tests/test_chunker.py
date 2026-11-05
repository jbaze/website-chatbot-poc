from app.chunker import Section, chunk_sections, count_tokens


def test_chunks_respect_size_and_keep_metadata():
    body = " ".join(f"word{i}" for i in range(1500))  # well over 500 tokens
    sections = [Section(heading="Pricing", text=body)]
    chunks = chunk_sections(sections, "https://example.com/pricing", "Example", max_tokens=500, overlap=60)

    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= 500
        assert count_tokens(c.content) <= 500
        assert c.url == "https://example.com/pricing"
        assert c.title == "Example"
        assert c.heading == "Pricing"


def test_chunks_overlap():
    body = " ".join(f"tok{i}" for i in range(900))
    chunks = chunk_sections([Section("H", body)], "u", "t", max_tokens=200, overlap=40)
    assert len(chunks) >= 2
    first_words = chunks[0].content.split()
    second_words = chunks[1].content.split()
    # the tail of chunk 1 reappears at the head of chunk 2
    assert any(w in second_words[:60] for w in first_words[-20:])


def test_sections_do_not_cross_headings():
    sections = [
        Section("About", "We are a small team building useful software for small businesses. " * 5),
        Section("Contact", "Email us at hello@example.com or call during office hours. " * 5),
    ]
    chunks = chunk_sections(sections, "u", "t")
    headings = {c.heading for c in chunks}
    assert headings == {"About", "Contact"}
    assert all(("hello@example.com" in c.content) == (c.heading == "Contact") for c in chunks)


def test_tiny_sections_are_merged_not_dropped():
    sections = [Section("Just a heading", "short"), Section("Body", "Real content follows here. " * 10)]
    chunks = chunk_sections(sections, "u", "t")
    assert len(chunks) == 1
    assert "short" in chunks[0].content
