from app.ingest import parse_messages, strip_html

SAMPLE = """
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message" data-post="kyiv_nebo/101">
  <div class="tgme_widget_message_text js-message_text" dir="auto">
   🔴 Балістика на <a href="/x">Київ</a>!<br/><br/>Прямуйте в укриття &amp; чекайте
  </div>
  <time datetime="2026-08-01T19:01:45+00:00">19:01</time>
 </div>
</div>
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message" data-post="kyiv_nebo/102">
  <a class="tgme_widget_message_photo_wrap" href="x"></a>
  <time datetime="2026-08-01T19:02:00+00:00">19:02</time>
 </div>
</div>
<div class="tgme_widget_message_wrap">
 <div class="tgme_widget_message" data-post="kyiv_nebo/103">
  <div class="tgme_widget_message_text js-message_text" dir="auto">Відбій!</div>
  <time datetime="2026-08-01T19:30:00+00:00">19:30</time>
 </div>
</div>
"""


def test_parse_messages_extracts_text_ids_and_time():
    messages = parse_messages(SAMPLE)
    ids = [m[0] for m in messages]
    assert ids == [101, 103]
    text = messages[0][1]
    assert "Балістика на Київ!" in text
    assert "\n\nПрямуйте в укриття & чекайте" in text
    assert "<" not in text
    assert messages[0][2].isoformat() == "2026-08-01T19:01:45+00:00"
    assert messages[1][1] == "Відбій!"

def test_strip_html_keeps_emoji_and_plain_text():
    assert strip_html('🛵 <i class="emoji"><b>⚡</b></i> шахед') == "🛵 ⚡ шахед"
