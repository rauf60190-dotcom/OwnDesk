import uuid
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock,patch

import pytest

from apps.integrations.telegram.adapter import TelegramAdapter,TelegramError,split_text
from apps.tickets.services import send


def outbound(**values):
    fields=dict(text="Ответ клиенту",author_name="Анна",direction="outbound",visibility="public",
        metadata={},attachments=Mock(first=Mock(return_value=None)),
        conversation=SimpleNamespace(external_id="123"),thread_id=None,reply_id=None,save=Mock())
    fields.update(values)
    return SimpleNamespace(**fields)


@pytest.mark.django_db
def test_sender_snapshot_survives_profile_rename_and_ticket_transfer(desk):
    author=desk['admin'].user
    author.display_name='Анна';author.save(update_fields=['display_name'])
    ticket=desk['ticket']
    message=send(desk['admin'],ticket.pk,ticket.version,'Проверяем',uuid.uuid4())
    author.display_name='Ольга';author.save(update_fields=['display_name'])
    ticket.refresh_from_db();ticket.assignee=desk['operator'];ticket.save(update_fields=['assignee'])
    message.refresh_from_db()
    adapter=TelegramAdapter(token='test-only')
    with patch.object(adapter,'call',return_value={'message_id':1}) as call:
        adapter.send(message)
        assert call.call_args.args[1]['text']=='Анна · Поддержка\n\nПроверяем'
        assert message.text=='Проверяем'


@pytest.mark.parametrize('name,expected',[('', 'Сотрудник поддержки'),('  Анна\n  Иванова  ','Анна Иванова · Поддержка'),('<Анна>','<Анна> · Поддержка')])
def test_plain_text_name_and_missing_author(name,expected):
    message=outbound(author_name=name)
    adapter=TelegramAdapter(token='test-only')
    with patch.object(adapter,'call',return_value={'message_id':1}) as call:
        adapter.send(message)
        data=call.call_args.args[1]
        assert data['text']==expected+'\n\nОтвет клиенту'
        assert 'parse_mode' not in data


@pytest.mark.parametrize('mime,method',[('image/png','sendPhoto'),('video/mp4','sendDocument'),('application/pdf','sendDocument')])
def test_attachment_without_body_has_employee_caption(mime,method):
    message=outbound(text='')
    message.attachments.first.return_value=SimpleNamespace(mime_type=mime,size=10,filename='file',object_key='key')
    adapter=TelegramAdapter(token='test-only')
    with patch('apps.attachments.storage.storage') as storage,patch.object(adapter,'call',return_value={'message_id':1}) as call:
        storage.return_value.get_object.return_value={'Body':BytesIO(b'file')}
        adapter.send(message)
        assert call.call_args.args[0]==method
        assert call.call_args.args[1]['caption']=='Анна · Поддержка\n\n'


def test_every_long_unicode_part_has_name_and_fits_telegram_limits():
    message=outbound(text='🙂'*6000,author_name='Я'*150)
    message.attachments.first.return_value=SimpleNamespace(mime_type='image/png',size=10,filename='photo.png',object_key='key')
    adapter=TelegramAdapter(token='test-only')
    with patch('apps.attachments.storage.storage') as storage,patch.object(adapter,'call',return_value={'message_id':1}) as call:
        storage.return_value.get_object.return_value={'Body':BytesIO(b'file')}
        adapter.send(message)
        chunks=[c.args[1].get('caption',c.args[1].get('text')) for c in call.call_args_list]
        header='Я'*150+' · Поддержка\n\n'
        assert all(chunk.startswith(header) for chunk in chunks)
        assert ''.join(chunk.removeprefix(header) for chunk in chunks)==message.text
        assert all(len(chunk.encode('utf-16-le'))//2<=(1024 if i==0 else 4096) for i,chunk in enumerate(chunks))


def test_retry_freezes_signed_parts_even_if_message_fields_change():
    message=outbound(text='Ответ '*2000)
    adapter=TelegramAdapter(token='test-only')
    with patch.object(adapter,'call',side_effect=[{'message_id':1},TelegramError('retry',retry_after=1)]):
        with pytest.raises(TelegramError):adapter.send(message)
    parts=list(message.metadata['telegram_delivery_parts'])
    message.author_name='Другое имя';message.text='Другой текст'
    with patch.object(adapter,'call',return_value={'message_id':2}) as call:
        adapter.send(message)
        assert [c.args[1]['text'] for c in call.call_args_list]==parts[1:]


def test_legacy_partial_delivery_keeps_original_boundaries():
    message=outbound(text='я'*6000,metadata={'telegram_parts':['old-id']})
    adapter=TelegramAdapter(token='test-only')
    with patch.object(adapter,'call',return_value={'message_id':2}) as call:
        adapter.send(message)
        assert [c.args[1]['text'] for c in call.call_args_list]==split_text(message.text)[1:]
        assert message.metadata['telegram_signature_version']==0


def test_automatic_reply_is_not_attributed_to_employee():
    message=outbound(direction='automatic',author_name='Бот')
    adapter=TelegramAdapter(token='test-only')
    with patch.object(adapter,'call',return_value={'message_id':1}) as call:
        adapter.send(message)
        assert call.call_args.args[1]['text']=='Ответ клиенту'


@pytest.mark.parametrize('changes',[{'visibility':'internal'},{'direction':'inbound'}])
def test_private_notes_and_inbound_messages_cannot_be_sent(changes):
    adapter=TelegramAdapter(token='test-only')
    with patch.object(adapter,'call') as call:
        with pytest.raises(TelegramError):adapter.send(outbound(**changes))
        call.assert_not_called()
