"""Parser for /account/edit profile page."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from hermes_telegram_ads.browser import BrowserAutomationTool


class ProfileInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    account_id: str = ""
    full_name: str = ""
    email: str = ""
    phone_number: str = ""
    country: str = ""
    city: str = ""
    ad_info: str = ""


async def get_profile_info(browser: BrowserAutomationTool) -> ProfileInfo:
    """Read /account/edit fields."""
    js = """
    () => ({
      account_id: document.querySelector("input[type='text'][value^='ACC']")?.value || '',
      full_name: document.querySelector("input[name='full_name']")?.value || '',
      email: document.querySelector("input[name='email']")?.value || '',
      phone_number: document.querySelector("input[name='phone_number']")?.value || '',
      country: document.querySelector("input[name='country']")?.value || '',
      city: document.querySelector("input[name='city']")?.value || '',
      ad_info: document.querySelector("textarea[name='ad_info']")?.value || '',
    })
    """
    raw = await browser.evaluate(js) or {}
    return ProfileInfo(**raw)


__all__ = ["ProfileInfo", "get_profile_info"]
