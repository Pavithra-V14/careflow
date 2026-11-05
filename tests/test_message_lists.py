"""The message list routes (threads and message headers) can never carry a message body (BUILD-SPEC.md 9.4; CLAUDE.md rule 6). Offline: reads the code and the schema, not a database."""

import inspect

from careflow import app as app_module

FORBIDDEN = ("body", "ciphertext", "key_version", "recorded_by", "idempotency")


def test_the_response_shapes_hold_no_body_ciphertext_key_or_actor_field():
    for model in (app_module.ThreadOut, app_module.MessageHeaderOut):
        assert not [f for f in model.model_fields if any(word in f for word in FORBIDDEN)], model.__name__
    assert set(app_module.MessageHeaderOut.model_fields) == {"message_id", "thread_id", "topic", "sender_kind", "created_at", "erased"}


def test_the_sql_of_the_list_routes_names_its_columns_and_never_selects_a_body_column():
    for handler in (app_module.list_threads, app_module.list_thread_messages):
        source = inspect.getsource(handler)
        assert "select *" not in source.lower() and "body_ct" not in source and "body_key_version" not in source, handler.__name__


def test_the_literal_threads_path_is_declared_before_the_message_id_path():
    paths = [r.path for r in app_module.staff_messages.routes]
    assert paths.index("/v1/staff/messages/threads") < paths.index("/v1/staff/messages/{message_id}")
    assert paths.index("/v1/staff/messages/threads/{thread_id}/messages") < paths.index("/v1/staff/messages/{message_id}")
