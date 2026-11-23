from app.services.uploads import sanitize_filename


def test_sanitize_filename() -> None:
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("C:\\Users\\me\\doc.txt") == "doc.txt"
    assert sanitize_filename("a\x00b\x1fc.txt") == "abc.txt"
    assert sanitize_filename("say %22hi%22.txt") == 'say "hi".txt'
    assert sanitize_filename("line%0D%0Abreak.txt") == "linebreak.txt"
    assert sanitize_filename("  ..  ") == ""
    assert sanitize_filename("отчёт 📄.pdf") == "отчёт 📄.pdf"
    long = "x" * 300 + ".jpeg"
    assert sanitize_filename(long) == "x" * 250 + ".jpeg"
