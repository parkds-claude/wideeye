import sqlite3
from urgent_board.collectors import contacts


def _book(path, rows):
    """주소록 DB 의 필요한 표만 흉내 낸다(맥 연락처 앱의 AddressBook-v22.abcddb 와 같은 이름)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript("""
      CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME VARCHAR, ZLASTNAME VARCHAR, ZORGANIZATION VARCHAR, ZNICKNAME VARCHAR);
      CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER VARCHAR);
      CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS VARCHAR);""")
    for pk, (first, last, org, phone, email) in enumerate(rows, 1):
        c.execute("INSERT INTO ZABCDRECORD VALUES (?,?,?,?,NULL)", (pk, first, last, org))
        if phone:
            c.execute("INSERT INTO ZABCDPHONENUMBER VALUES (NULL,?,?)", (pk, phone))
        if email:
            c.execute("INSERT INTO ZABCDEMAILADDRESS VALUES (NULL,?,?)", (pk, email))
    c.commit(); c.close()


def test_phone_key_matches_the_forms_people_write():
    assert contacts.phone_key("+821012345678") == contacts.phone_key("010-1234-5678") == contacts.phone_key("+82 10-1234-5678") == "01012345678"
    assert contacts.phone_key("02-123-4567") == contacts.phone_key("+82 2-123-4567") == "021234567"
    assert contacts.phone_key("someone@example.com") == "" and contacts.phone_key("1588") == ""


def test_display_name_follows_how_the_contacts_app_shows_it():
    assert contacts.display_name("민수", "김", None, None) == "김민수"
    assert contacts.display_name("Jane", "Doe", None, None) == "Jane Doe"
    assert contacts.display_name("협의회 가나다 연구관", None, None, None) == "협의회 가나다 연구관"
    assert contacts.display_name(None, None, "가나다상사", None) == "가나다상사"
    assert contacts.display_name(None, None, None, None) == ""


def test_address_book_reads_every_source_and_finds_numbers_and_addresses(tmp_path):
    _book(tmp_path / "AddressBook-v22.abcddb", [])
    _book(tmp_path / "Sources" / "A" / "AddressBook-v22.abcddb",
          [("협의회 가나다 연구관", None, None, "+82 10-1234-5678", None), ("민수", "김", None, None, "Me@Example.com")])
    book = contacts.AddressBook(tmp_path)
    assert book.name_for("+821012345678") == "협의회 가나다 연구관"
    assert book.name_for("me@example.com") == "김민수"
    assert book.name_for("+821099999999") == "" and book.name_for("") == ""


def test_address_book_that_cannot_be_read_gives_no_names(tmp_path):
    book = contacts.AddressBook(tmp_path / "없는 폴더")                 # 권한이 없거나 연락처를 안 쓰는 맥 — 번호가 보일 뿐 멈추지 않는다
    assert book.name_for("+821012345678") == ""


def test_address_book_does_no_io_until_asked(tmp_path):
    calls = []
    book = contacts.AddressBook(tmp_path, clock=lambda: calls.append(1) or 0.0)
    assert calls == []                                                   # 생성자에서는 아무것도 읽지 않는다
