"""Launcher preflight fails before a service or browser can be constructed."""

import socket
from unittest.mock import Mock

import pytest

from autoweb_api import studio


def ui(tmp_path):
    path = tmp_path / 'apps/web/dist'
    path.mkdir(parents=True)
    (path / 'index.html').write_text('<html>Studio launcher test</html>')
    return path


def test_help_does_not_load_optional_server_dependencies(monkeypatch):
    probe = Mock(side_effect=AssertionError('help must not inspect runtime dependencies'))
    monkeypatch.setattr(studio.importlib.util, 'find_spec', probe)
    with pytest.raises(SystemExit) as result:
        studio.main(['--help'])
    assert result.value.code == 0
    probe.assert_not_called()


def test_missing_server_dependencies_are_actionable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(studio.importlib.util, 'find_spec', lambda name: None)
    assert studio.main(['--root', str(tmp_path)]) == 2
    assert '[server]' in capsys.readouterr().err
    assert not (tmp_path / 'artifacts').exists()


def test_missing_ui_build_is_actionable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(studio.importlib.util, 'find_spec', lambda name: object())
    assert studio.main(['--root', str(tmp_path)]) == 2
    assert 'npm run build' in capsys.readouterr().err


@pytest.mark.parametrize('value', ['0', '65536', '-1', 'not-a-port'])
def test_invalid_ports_do_not_start(value):
    with pytest.raises(SystemExit) as result:
        studio.main(['--port', value])
    assert result.value.code == 2


def test_nonloopback_bind_is_not_a_cli_option():
    with pytest.raises(SystemExit):
        studio.main(['--host', '0.0.0.0'])


def test_port_conflict_does_not_construct_service(tmp_path, monkeypatch, capsys):
    ui(tmp_path)
    monkeypatch.setattr('autoweb_api.process_tree.supported', lambda: True)
    run = Mock(side_effect=AssertionError('no service on occupied port'))
    monkeypatch.setattr(studio, 'run_server', run)
    with socket.socket() as owner:
        owner.bind(('127.0.0.1', 0))
        owner.listen()
        assert studio.main(['--root', str(tmp_path), '--port', str(owner.getsockname()[1])]) == 2
    assert 'already in use' in capsys.readouterr().err
    run.assert_not_called()
    assert not (tmp_path / 'artifacts').exists()


def test_competing_launcher_cannot_claim_port_during_service_startup(tmp_path, monkeypatch):
    ui(tmp_path)
    monkeypatch.setattr('autoweb_api.process_tree.supported', lambda: True)
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]

    def competing_start(settings, listener):
        with socket.socket() as rival:
            rival.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                rival.bind(('127.0.0.1', settings.port))
        return 0

    monkeypatch.setattr(studio, 'run_server', competing_start)
    assert studio.main(['--root', str(tmp_path), '--port', str(port)]) == 0
