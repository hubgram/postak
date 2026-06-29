import contextlib
import re
import unicodedata
from collections.abc import AsyncIterator

from aiogram.enums import ChatAction
from aiogram.types import InputRichMessage, Message
from telegramify_markdown.stream import EditStream


def fix_html(html: str) -> str:
    """Rewrite telegramify's rich HTML so Telegram renders it natively (verified live):

    - ☑/✅ list items -> <input type="checkbox">, rendered as real checkboxes
    - <table bordered striped> -> is_bordered + is_striped (the boolean form works;
      the `is_bordered="true"` spelling and the checkbox-symbol config are ignored)
    - align/valign on cells -> centered horizontally + vertically
    """
    html = html.replace("<li>☑ ", '<li><input type="checkbox"> ')
    html = html.replace("<li>✅ ", '<li><input type="checkbox" checked> ')
    html = html.replace("<table>", "<table bordered striped>")
    return re.sub(r"<(t[hd])\b[^>]*>", r'<\1 align="center" valign="middle">', html)


def is_rtl(html: str) -> bool:
    """True if the visible text is predominantly right-to-left (Persian/Arabic/Hebrew)."""
    text = re.sub(r"<[^>]+>", "", html)
    rtl = sum(unicodedata.bidirectional(c) in ("R", "AL") for c in text)
    ltr = sum(unicodedata.bidirectional(c) == "L" for c in text)
    return rtl > ltr


def styled_rich(payload) -> InputRichMessage:
    """Build an InputRichMessage from an EditStream payload, applying fix_html + RTL."""
    data = payload.rich_message.to_dict()
    if "html" in data:
        data["html"] = fix_html(data["html"])
        if is_rtl(data["html"]):
            data["is_rtl"] = True
    return InputRichMessage(**data)


async def stream_tokens(message: Message, tokens: AsyncIterator[str]) -> str:
    """Stream an async token iterator into one reply via EditStream; return the text.

    Sends a 'typing' action first, so the user sees the bot working before the first
    streamed message appears.
    """
    bot = message.bot
    assert bot is not None  # always set on messages received in a handler

    async def send_message(payload) -> int:
        sent = await message.reply_rich(styled_rich(payload))
        return sent.message_id

    async def edit_message(message_id: int, payload) -> None:
        await bot.edit_message_text(
            rich_message=styled_rich(payload),
            chat_id=message.chat.id,
            message_id=message_id,
        )

    with contextlib.suppress(Exception):
        await bot.send_chat_action(
            message.chat.id, ChatAction.TYPING, message_thread_id=message.message_thread_id
        )
    async with EditStream(send_message, edit_message, mode="rich") as stream:
        await stream.consume(tokens)
    return stream.buffer
