"""Selected profiles only; never run live websites/mailboxes by default."""

import pytest


@pytest.mark.live
@pytest.mark.asyncio
async def test_selected_site_authenticates(autoweb_login):
    assert autoweb_login.status == "succeeded"
    assert not autoweb_login.session.page.is_closed()
