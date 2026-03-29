"""
LinkedIn Easy Apply form field detection and mapping.

This module provides utilities to detect form fields in LinkedIn's Easy Apply
flow and map them to user profile data for auto-suggestions.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Page, Locator

from ..logging_setup import logger
from ..models import FormField, FormFieldType


# CSS selectors for LinkedIn Easy Apply form elements
EASY_APPLY_SELECTORS = {
    "modal": [
        "div.jobs-easy-apply-modal",
        "div[data-test-modal]",
        "div.artdeco-modal",
    ],
    "form_container": [
        "div.jobs-easy-apply-content",
        "div.jobs-easy-apply-form-section__grouping",
        "form.jobs-easy-apply-form",
    ],
    "input_text": [
        "input[type='text']",
        "input[type='email']",
        "input[type='tel']",
        "input[type='url']",
        "input[type='number']",
    ],
    "textarea": ["textarea"],
    "select": ["select"],
    "radio_group": [
        "fieldset[data-test-form-builder-radio-button-form-component]",
        "div.fb-form-element--radio",
    ],
    "checkbox": [
        "input[type='checkbox']",
        "div.fb-form-element--checkbox",
    ],
    "file_upload": [
        "input[type='file']",
        "button[aria-label*='upload']",
    ],
    "next_button": [
        "button[aria-label='Continue to next step']",
        "button[data-easy-apply-next-button]",
        "footer button.artdeco-button--primary",
    ],
    "submit_button": [
        "button[aria-label='Submit application']",
        "button[data-easy-apply-submit-button]",
        "button:has-text('Submit application')",
    ],
    "review_button": [
        "button[aria-label='Review your application']",
        "button:has-text('Review')",
    ],
}

# Common field label patterns for mapping to profile data
FIELD_PATTERNS = {
    "email": [
        r"email",
        r"e-mail",
        r"email address",
    ],
    "phone": [
        r"phone",
        r"telephone",
        r"mobile",
        r"cell",
        r"phone number",
    ],
    "name": [
        r"full name",
        r"your name",
        r"name",
    ],
    "first_name": [
        r"first name",
        r"given name",
    ],
    "last_name": [
        r"last name",
        r"surname",
        r"family name",
    ],
    "city": [
        r"city",
        r"current city",
    ],
    "location": [
        r"location",
        r"current location",
        r"where.*located",
    ],
    "linkedin": [
        r"linkedin",
        r"linkedin profile",
        r"linkedin url",
    ],
    "github": [
        r"github",
        r"github profile",
    ],
    "portfolio": [
        r"portfolio",
        r"website",
        r"personal site",
    ],
    "years_experience": [
        r"years.*experience",
        r"experience.*years",
        r"how many years",
        r"total.*experience",
    ],
    "work_authorization": [
        r"authorized.*work",
        r"work authorization",
        r"legally.*work",
        r"require.*sponsorship",
        r"visa.*sponsorship",
    ],
    "remote_preference": [
        r"remote",
        r"work.*remote",
        r"on-?site",
        r"hybrid",
    ],
    "salary": [
        r"salary",
        r"compensation",
        r"expected.*salary",
        r"desired.*salary",
    ],
    "start_date": [
        r"start date",
        r"when.*start",
        r"availability",
        r"notice period",
    ],
    "cover_letter": [
        r"cover letter",
        r"why.*interested",
        r"why.*apply",
        r"tell us about",
    ],
    "resume": [
        r"resume",
        r"cv",
        r"curriculum vitae",
    ],
}


async def detect_form_fields(page: Page) -> List[FormField]:
    """
    Detect all form fields in the current Easy Apply form page.
    
    Args:
        page: Playwright page object
    
    Returns:
        List of detected FormField objects
    """
    fields: List[FormField] = []
    field_index = 0
    
    try:
        # Wait for form to be visible
        await _wait_for_form(page)
        
        # Detect text inputs
        text_fields = await _detect_text_inputs(page, field_index)
        fields.extend(text_fields)
        field_index += len(text_fields)
        
        # Detect textareas
        textarea_fields = await _detect_textareas(page, field_index)
        fields.extend(textarea_fields)
        field_index += len(textarea_fields)
        
        # Detect selects
        select_fields = await _detect_selects(page, field_index)
        fields.extend(select_fields)
        field_index += len(select_fields)
        
        # Detect radio groups
        radio_fields = await _detect_radio_groups(page, field_index)
        fields.extend(radio_fields)
        field_index += len(radio_fields)
        
        # Detect checkboxes
        checkbox_fields = await _detect_checkboxes(page, field_index)
        fields.extend(checkbox_fields)
        field_index += len(checkbox_fields)
        
        # Detect file uploads
        file_fields = await _detect_file_uploads(page, field_index)
        fields.extend(file_fields)
        
        logger.info("Detected {} form fields on page", len(fields))
        
    except Exception as e:
        logger.error("Error detecting form fields: {}", e)
    
    return fields


async def _wait_for_form(page: Page, timeout: int = 5000) -> bool:
    """Wait for the Easy Apply form to be visible."""
    for selector in EASY_APPLY_SELECTORS["modal"] + EASY_APPLY_SELECTORS["form_container"]:
        try:
            await page.wait_for_selector(selector, timeout=timeout)
            return True
        except Exception:
            continue
    return False


async def _detect_text_inputs(page: Page, start_index: int) -> List[FormField]:
    """Detect text input fields."""
    fields: List[FormField] = []
    
    for selector in EASY_APPLY_SELECTORS["input_text"]:
        try:
            elements = await page.locator(selector).all()
            for i, element in enumerate(elements):
                if not await element.is_visible():
                    continue
                
                field = await _extract_field_info(
                    element,
                    field_id=f"text_{start_index + len(fields)}",
                    field_type=await _get_input_type(element),
                )
                if field:
                    fields.append(field)
        except Exception as e:
            logger.debug("Error detecting text inputs with {}: {}", selector, e)
    
    return fields


async def _detect_textareas(page: Page, start_index: int) -> List[FormField]:
    """Detect textarea fields."""
    fields: List[FormField] = []
    
    for selector in EASY_APPLY_SELECTORS["textarea"]:
        try:
            elements = await page.locator(selector).all()
            for element in elements:
                if not await element.is_visible():
                    continue
                
                field = await _extract_field_info(
                    element,
                    field_id=f"textarea_{start_index + len(fields)}",
                    field_type=FormFieldType.TEXTAREA,
                )
                if field:
                    fields.append(field)
        except Exception as e:
            logger.debug("Error detecting textareas with {}: {}", selector, e)
    
    return fields


async def _detect_selects(page: Page, start_index: int) -> List[FormField]:
    """Detect select/dropdown fields."""
    fields: List[FormField] = []
    
    for selector in EASY_APPLY_SELECTORS["select"]:
        try:
            elements = await page.locator(selector).all()
            for element in elements:
                if not await element.is_visible():
                    continue
                
                # Get options
                options = []
                option_elements = await element.locator("option").all()
                for opt in option_elements:
                    text = (await opt.inner_text()).strip()
                    if text and text.lower() != "select an option":
                        options.append(text)
                
                field = await _extract_field_info(
                    element,
                    field_id=f"select_{start_index + len(fields)}",
                    field_type=FormFieldType.SELECT,
                    options=options,
                )
                if field:
                    fields.append(field)
        except Exception as e:
            logger.debug("Error detecting selects with {}: {}", selector, e)
    
    return fields


async def _detect_radio_groups(page: Page, start_index: int) -> List[FormField]:
    """Detect radio button groups."""
    fields: List[FormField] = []
    
    for selector in EASY_APPLY_SELECTORS["radio_group"]:
        try:
            fieldsets = await page.locator(selector).all()
            for fieldset in fieldsets:
                if not await fieldset.is_visible():
                    continue
                
                # Get the legend/label
                label = ""
                try:
                    legend = fieldset.locator("legend")
                    if await legend.count() > 0:
                        label = (await legend.first.inner_text()).strip()
                except Exception:
                    pass
                
                if not label:
                    try:
                        label_el = fieldset.locator("label").first
                        if await label_el.count() > 0:
                            label = (await label_el.inner_text()).strip()
                    except Exception:
                        pass
                
                # Get radio options
                options = []
                radio_labels = await fieldset.locator("label").all()
                for radio_label in radio_labels:
                    text = (await radio_label.inner_text()).strip()
                    if text:
                        options.append(text)
                
                if options:
                    field = FormField(
                        field_id=f"radio_{start_index + len(fields)}",
                        label=label or "Select an option",
                        field_type=FormFieldType.RADIO,
                        options=options,
                        required=await _is_required(fieldset),
                    )
                    fields.append(field)
        except Exception as e:
            logger.debug("Error detecting radio groups with {}: {}", selector, e)
    
    return fields


async def _detect_checkboxes(page: Page, start_index: int) -> List[FormField]:
    """Detect checkbox fields."""
    fields: List[FormField] = []
    
    for selector in EASY_APPLY_SELECTORS["checkbox"]:
        try:
            elements = await page.locator(selector).all()
            for element in elements:
                if not await element.is_visible():
                    continue
                
                field = await _extract_field_info(
                    element,
                    field_id=f"checkbox_{start_index + len(fields)}",
                    field_type=FormFieldType.CHECKBOX,
                )
                if field:
                    fields.append(field)
        except Exception as e:
            logger.debug("Error detecting checkboxes with {}: {}", selector, e)
    
    return fields


async def _detect_file_uploads(page: Page, start_index: int) -> List[FormField]:
    """Detect file upload fields."""
    fields: List[FormField] = []
    
    for selector in EASY_APPLY_SELECTORS["file_upload"]:
        try:
            elements = await page.locator(selector).all()
            for element in elements:
                # File inputs may be hidden, check parent visibility
                parent = element.locator("..")
                if await parent.count() > 0:
                    if not await parent.first.is_visible():
                        continue
                
                field = await _extract_field_info(
                    element,
                    field_id=f"file_{start_index + len(fields)}",
                    field_type=FormFieldType.FILE,
                )
                if field:
                    fields.append(field)
        except Exception as e:
            logger.debug("Error detecting file uploads with {}: {}", selector, e)
    
    return fields


async def _extract_field_info(
    element: Locator,
    field_id: str,
    field_type: FormFieldType,
    options: Optional[List[str]] = None,
) -> Optional[FormField]:
    """Extract field information from an element."""
    try:
        # Get label
        label = await _get_field_label(element)
        if not label:
            label = field_id  # Fallback to field_id
        
        # Get current value
        current_value = await _get_field_value(element, field_type)
        
        # Get placeholder
        placeholder = await element.get_attribute("placeholder")
        
        # Get CSS selector for the element
        selector = await _get_element_selector(element)
        
        # Check if required
        required = await _is_required(element)
        
        return FormField(
            field_id=field_id,
            label=label,
            field_type=field_type,
            required=required,
            current_value=current_value,
            options=options or [],
            placeholder=placeholder,
            selector=selector,
        )
    except Exception as e:
        logger.debug("Error extracting field info: {}", e)
        return None


async def _get_field_label(element: Locator) -> str:
    """Get the label text for a form field."""
    # Try aria-label
    aria_label = await element.get_attribute("aria-label")
    if aria_label:
        return aria_label.strip()
    
    # Try associated label via id
    element_id = await element.get_attribute("id")
    if element_id:
        try:
            page = element.page
            label = page.locator(f"label[for='{element_id}']")
            if await label.count() > 0:
                return (await label.first.inner_text()).strip()
        except Exception:
            pass
    
    # Try parent label
    try:
        parent_label = element.locator("xpath=ancestor::label")
        if await parent_label.count() > 0:
            return (await parent_label.first.inner_text()).strip()
    except Exception:
        pass
    
    # Try preceding sibling label
    try:
        page = element.page
        # Look for label in the same form group
        form_group = element.locator("xpath=ancestor::div[contains(@class, 'form')]")
        if await form_group.count() > 0:
            label = form_group.first.locator("label")
            if await label.count() > 0:
                return (await label.first.inner_text()).strip()
    except Exception:
        pass
    
    # Try name or placeholder as fallback
    name = await element.get_attribute("name")
    if name:
        return name.replace("_", " ").replace("-", " ").title()
    
    placeholder = await element.get_attribute("placeholder")
    if placeholder:
        return placeholder.strip()
    
    return ""


async def _get_field_value(element: Locator, field_type: FormFieldType) -> Optional[str]:
    """Get the current value of a form field."""
    try:
        if field_type == FormFieldType.CHECKBOX:
            is_checked = await element.is_checked()
            return "checked" if is_checked else "unchecked"
        elif field_type == FormFieldType.SELECT:
            return await element.input_value()
        elif field_type == FormFieldType.FILE:
            return None  # File inputs don't have a readable value
        else:
            return await element.input_value()
    except Exception:
        return None


async def _get_input_type(element: Locator) -> FormFieldType:
    """Determine the specific input type."""
    input_type = await element.get_attribute("type")
    
    type_mapping = {
        "email": FormFieldType.EMAIL,
        "tel": FormFieldType.PHONE,
        "url": FormFieldType.URL,
        "number": FormFieldType.NUMBER,
        "date": FormFieldType.DATE,
    }
    
    return type_mapping.get(input_type, FormFieldType.TEXT)


async def _is_required(element: Locator) -> bool:
    """Check if a field is required."""
    required_attr = await element.get_attribute("required")
    if required_attr is not None:
        return True
    
    aria_required = await element.get_attribute("aria-required")
    if aria_required == "true":
        return True
    
    # Check for asterisk in label
    label = await _get_field_label(element)
    if label and "*" in label:
        return True
    
    return False


async def _get_element_selector(element: Locator) -> Optional[str]:
    """Generate a CSS selector for an element."""
    try:
        # Try ID first
        element_id = await element.get_attribute("id")
        if element_id:
            return f"#{element_id}"
        
        # Try name
        name = await element.get_attribute("name")
        if name:
            tag = await element.evaluate("el => el.tagName.toLowerCase()")
            return f"{tag}[name='{name}']"
        
        # Try data attributes
        data_test = await element.get_attribute("data-test")
        if data_test:
            return f"[data-test='{data_test}']"
        
        return None
    except Exception:
        return None


def map_field_to_profile(field: FormField, profile: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """
    Map a form field to profile data for auto-suggestion.
    
    Args:
        field: The form field to map
        profile: User profile data dictionary
    
    Returns:
        Tuple of (suggested_value, source) where source is "profile", "canned", or "ai"
    """
    label_lower = field.label.lower()
    
    # Check each pattern category
    for category, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, label_lower, re.IGNORECASE):
                value, source = _get_profile_value(category, profile, field)
                if value:
                    return value, source
    
    # Check canned answers
    canned = profile.get("canned_answers", {})
    for key, answer in canned.items():
        if key.lower() in label_lower or label_lower in key.lower():
            return answer, "canned"
    
    return None, ""


def _get_profile_value(
    category: str,
    profile: Dict[str, Any],
    field: FormField,
) -> Tuple[Optional[str], str]:
    """Get profile value for a field category."""
    source = "profile"
    
    if category == "email":
        return profile.get("email"), source
    
    elif category == "phone":
        return profile.get("phone"), source
    
    elif category == "name":
        return profile.get("full_name"), source
    
    elif category == "first_name":
        full_name = profile.get("full_name", "")
        parts = full_name.split()
        return parts[0] if parts else None, source
    
    elif category == "last_name":
        full_name = profile.get("full_name", "")
        parts = full_name.split()
        return parts[-1] if len(parts) > 1 else None, source
    
    elif category == "city":
        return profile.get("city"), source
    
    elif category == "location":
        city = profile.get("city", "")
        country = profile.get("country", "")
        if city and country:
            return f"{city}, {country}", source
        return city or country or None, source
    
    elif category == "linkedin":
        return str(profile.get("linkedin_url", "")), source
    
    elif category == "github":
        github = profile.get("github_url")
        return str(github) if github else None, source
    
    elif category == "portfolio":
        portfolio = profile.get("portfolio_url")
        return str(portfolio) if portfolio else None, source
    
    elif category == "years_experience":
        years = profile.get("years_experience_total")
        return str(years) if years else None, source
    
    elif category == "work_authorization":
        # Check if field has options (radio/select)
        if field.options:
            regions = profile.get("authorized_to_work_regions", [])
            # Try to match an option
            for opt in field.options:
                opt_lower = opt.lower()
                if "yes" in opt_lower:
                    return opt, source
                for region in regions:
                    if region.lower() in opt_lower:
                        return opt, source
        
        # Use canned answer
        canned = profile.get("canned_answers", {})
        return canned.get("work_authorization"), "canned"
    
    elif category == "remote_preference":
        work_prefs = profile.get("work_preferences", [])
        if isinstance(work_prefs, list):
            if "Remote" in work_prefs:
                return "Yes", source
            elif "Hybrid" in work_prefs:
                return "Hybrid", source
        return None, ""
    
    elif category == "salary":
        salary_prefs = profile.get("salary_preferences", {})
        if isinstance(salary_prefs, dict):
            min_salary = salary_prefs.get("min")
            if min_salary:
                return str(min_salary), source
        return None, ""
    
    elif category == "resume":
        return "profile_resume", "file"
    
    return None, ""


async def detect_form_buttons(page: Page) -> Dict[str, bool]:
    """
    Detect which navigation buttons are present on the current form page.
    
    Returns:
        Dict with keys 'next', 'submit', 'review' indicating presence
    """
    buttons = {
        "next": False,
        "submit": False,
        "review": False,
    }
    
    for selector in EASY_APPLY_SELECTORS["next_button"]:
        try:
            if await page.locator(selector).count() > 0:
                buttons["next"] = True
                break
        except Exception:
            pass
    
    for selector in EASY_APPLY_SELECTORS["submit_button"]:
        try:
            if await page.locator(selector).count() > 0:
                buttons["submit"] = True
                break
        except Exception:
            pass
    
    for selector in EASY_APPLY_SELECTORS["review_button"]:
        try:
            if await page.locator(selector).count() > 0:
                buttons["review"] = True
                break
        except Exception:
            pass
    
    return buttons


async def get_form_progress(page: Page) -> Tuple[int, Optional[int]]:
    """
    Get the current form progress (step X of Y).
    
    Returns:
        Tuple of (current_step, total_steps) where total_steps may be None
    """
    try:
        # Look for progress indicator
        progress_selectors = [
            "span[data-test-progress-step]",
            "div.jobs-easy-apply-progress",
            "div[aria-label*='step']",
        ]
        
        for selector in progress_selectors:
            element = page.locator(selector)
            if await element.count() > 0:
                text = await element.first.inner_text()
                # Parse "Step X of Y" or similar
                match = re.search(r"(\d+)\s*(?:of|/)\s*(\d+)", text)
                if match:
                    return int(match.group(1)), int(match.group(2))
        
        # Try aria attributes
        progress_bar = page.locator("[role='progressbar']")
        if await progress_bar.count() > 0:
            value_now = await progress_bar.first.get_attribute("aria-valuenow")
            value_max = await progress_bar.first.get_attribute("aria-valuemax")
            if value_now and value_max:
                return int(value_now), int(value_max)
        
    except Exception as e:
        logger.debug("Error detecting form progress: {}", e)
    
    return 1, None


def suggest_field_values(
    fields: List[FormField],
    profile: Dict[str, Any],
) -> List[FormField]:
    """
    Add suggestions to form fields based on profile data.
    
    Args:
        fields: List of detected form fields
        profile: User profile data
    
    Returns:
        Updated list of fields with suggestions
    """
    for field in fields:
        if field.suggested_value:
            continue  # Already has suggestion
        
        suggested, source = map_field_to_profile(field, profile)
        if suggested:
            field.suggested_value = suggested
            field.suggestion_source = source
    
    return fields
