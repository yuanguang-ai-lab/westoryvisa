"""Versioned, code-owned CEAC permissions for each supported page."""

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Tuple
from urllib.parse import urlsplit

from .models import ActionKind, BrowserObservation


@dataclass(frozen=True)
class CEACPageClassification:
    """One code-owned interpretation of the currently rendered CEAC page.

    ``kind`` is intentionally small and stable because browser restoration,
    workflow permission checks, and the continuous watcher must agree on the
    same boundary.  ``stage_score`` is meaningful only for a route that looks
    like a DS-160 workflow stage; it never proves that the rendered document
    is a live form by itself.
    """

    kind: str
    stage_score: int = 0
    reason: str = ""


@dataclass(frozen=True)
class PagePlan:
    id: str
    path_patterns: Tuple[str, ...]
    title_patterns: Tuple[str, ...]
    allowed_field_ids: frozenset = field(default_factory=frozenset)
    allowed_field_prefixes: Tuple[str, ...] = ()
    required_field_ids: frozenset = field(default_factory=frozenset)
    field_labels: dict = field(default_factory=dict)
    control_hints: dict = field(default_factory=dict)
    allowed_action_kinds: frozenset = field(default_factory=lambda: frozenset({
        ActionKind.TYPE,
        ActionKind.SELECT,
        ActionKind.CLICK,
        ActionKind.PRESS_KEY,
        ActionKind.SCROLL,
        ActionKind.WAIT,
        ActionKind.PAUSE,
        ActionKind.COMPLETE,
    }))
    allowed_click_patterns: Tuple[str, ...] = (
        r"^continue$",
        r"^next(?:: .+)?$",
        r"^save$",
        r"^back$",
        r"^previous$",
    )
    allow_next: bool = True
    allow_complete: bool = True

    def matches(self, observation: BrowserObservation):
        path_match = any(
            re.search(pattern, observation.url, flags=re.IGNORECASE)
            for pattern in self.path_patterns
        )
        title_match = not self.title_patterns or any(
            re.search(pattern, observation.title, flags=re.IGNORECASE)
            for pattern in self.title_patterns
        )
        return path_match and title_match

    def allows_click(self, target_hint):
        return any(
            re.fullmatch(pattern, str(target_hint).strip(), flags=re.IGNORECASE)
            for pattern in self.allowed_click_patterns
        )

    def allows_field(self, field_id):
        candidate = str(field_id)
        return (
            candidate in self.allowed_field_ids
            or any(
                candidate.startswith(prefix)
                for prefix in self.allowed_field_prefixes
            )
        )


class PagePlanRegistry:
    VERSION = "2026-07-31.13"
    # Coarse extraction IDs predate the exact ``ceac.<page>.*`` manifest.
    # Their ownership must remain field-specific because the DS-160 splits
    # Personal 1/2, Travel/U.S. Contact, and Address/Phone into different
    # physical pages.
    COARSE_FIELD_OWNERS = {
        "personal.surname": "ceac-plan-personal1",
        "personal.givenNames": "ceac-plan-personal1",
        "personal.nativeName": "ceac-plan-personal1",
        "personal.otherNames": "ceac-plan-personal1",
        "personal.telecodeName": "ceac-plan-personal1",
        "personal.sex": "ceac-plan-personal1",
        "personal.maritalStatus": "ceac-plan-personal1",
        "personal.dateOfBirth": "ceac-plan-personal1",
        "personal.placeOfBirth": "ceac-plan-personal1",
        "personal.birthCity": "ceac-plan-personal1",
        "personal.birthRegion": "ceac-plan-personal1",
        "personal.birthCountry": "ceac-plan-personal1",
        "personal.nationality": "ceac-plan-personal2",
        "personal.nationalId": "ceac-plan-personal2",
        "personal.otherNationality": "ceac-plan-personal2",
        "personal.permanentResidentCountry": "ceac-plan-personal2",
        "personal.ssn": "ceac-plan-personal2",
        "personal.taxId": "ceac-plan-personal2",
        "contact.homeAddress": "ceac-plan-address_phone",
        "contact.homeStreet1": "ceac-plan-address_phone",
        "contact.homeStreet2": "ceac-plan-address_phone",
        "contact.homeCity": "ceac-plan-address_phone",
        "contact.homeRegion": "ceac-plan-address_phone",
        "contact.homePostalCode": "ceac-plan-address_phone",
        "contact.homeCountry": "ceac-plan-address_phone",
        "contact.primaryPhone": "ceac-plan-address_phone",
        "contact.secondaryPhone": "ceac-plan-address_phone",
        "contact.workPhone": "ceac-plan-address_phone",
        "contact.applicantEmail": "ceac-plan-address_phone",
        "contact.address": "ceac-plan-travel",
        "contact.usAddress": "ceac-plan-travel",
        "contact.usStreet1": "ceac-plan-travel",
        "contact.usStreet2": "ceac-plan-travel",
        "contact.usCity": "ceac-plan-travel",
        "contact.usState": "ceac-plan-travel",
        "contact.usPostalCode": "ceac-plan-travel",
        "contact.surname": "ceac-plan-us_contact",
        "contact.givenNames": "ceac-plan-us_contact",
        "contact.organizationName": "ceac-plan-us_contact",
        "contact.phone": "ceac-plan-us_contact",
        "contact.email": "ceac-plan-us_contact",
        "contact.usEmail": "ceac-plan-us_contact",
        "passport.number": "ceac-plan-passport",
        "passport.type": "ceac-plan-passport",
        "passport.bookNumber": "ceac-plan-passport",
        "passport.issuance": "ceac-plan-passport",
        "passport.issueDate": "ceac-plan-passport",
        "passport.expiration": "ceac-plan-passport",
        "passport.issuingCountry": "ceac-plan-passport",
        "passport.issuingAuthority": "ceac-plan-passport",
        "passport.issueCity": "ceac-plan-passport",
        "passport.issueRegion": "ceac-plan-passport",
        "passport.issueCountry": "ceac-plan-passport",
        "travel.purpose": "ceac-plan-travel",
        "travel.purposeSummary": "ceac-plan-travel",
        "travel.arrivalDate": "ceac-plan-travel",
        "travel.departureDate": "ceac-plan-travel",
        "travel.stayLength": "ceac-plan-travel",
        "education.schoolName": "ceac-plan-sevis",
        "education.sevisId": "ceac-plan-sevis",
        "education.programName": "ceac-plan-sevis",
        "education.schoolAddress": "ceac-plan-sevis",
        "education.schoolStreet1": "ceac-plan-sevis",
        "education.schoolStreet2": "ceac-plan-sevis",
        "education.schoolCity": "ceac-plan-sevis",
        "education.schoolState": "ceac-plan-sevis",
        "education.schoolPostalCode": "ceac-plan-sevis",
        "education.programStartDate": "ceac-plan-sevis",
        "education.programEndDate": "ceac-plan-sevis",
        "education.programNumber": "ceac-plan-sevis",
        "education.sponsorName": "ceac-plan-sevis",
        "education.programCategory": "ceac-plan-sevis",
        "history.refusal": "ceac-plan-previous_us_travel",
        "history.overstay": "ceac-plan-security_background4",
        "security.criminal": "ceac-plan-security_background2",
    }
    CEAC_PAGE_CANONICAL_OWNERS = {
        "personal1": "ceac-plan-personal1",
        "passport": "ceac-plan-passport",
        "travel": "ceac-plan-travel",
        "sevis": "ceac-plan-sevis",
    }
    # Older checkpoints used broad legacy plans for pages that now also have
    # exact CEAC node plans.  Equivalence is deliberately pairwise (not a
    # transitive canonical collapse): the old travel plan covered both Travel
    # and SEVIS, which are distinct physical pages.
    LEGACY_PLAN_EQUIVALENTS = {
        "personal-information": frozenset({"ceac-plan-personal1"}),
        "passport-information": frozenset({"ceac-plan-passport"}),
        "travel-information": frozenset({"ceac-plan-travel"}),
        "sevis-information": frozenset({"ceac-plan-sevis"}),
    }
    TERMINAL_URL_PATTERNS = (
        # CEAC has used both directory-based and filename/node-based Review
        # and Sign routes.  Keep this code-owned boundary broader than a
        # single deployment spelling, but narrow enough not to match the
        # always-visible REVIEW/SIGN navigation tabs on ordinary form pages.
        r"/GenNIV/(?:[^?#]+/)*(?:Review|Sign)(?:/|[A-Za-z0-9_.-])",
        r"/GenNIV/[^?#]*(?:review|sign)[A-Za-z0-9_.-]*\.aspx",
        r"[?&]node=(?:Review|Sign)[A-Za-z0-9_.-]*(?:&|$)",
    )
    TERMINAL_TITLE_PATTERNS = (
        r"\breview\b.{0,80}\b(?:application|information)\b",
        r"\bsign\b.{0,40}\bsubmit\b",
        r"\belectronic signature\b",
    )
    # Exact physical routes in DS-160 order.  Page matching and persistent-tab
    # recovery consume this same tuple so support for a conditional page cannot
    # drift between the registry and browser lifecycle.
    CEAC_DYNAMIC_PAGE_ROUTES = (
        (
            "personal1",
            r"/GenNIV/.+complete_personal\.aspx",
            r"[?&]node=Personal1(?:&|$)",
        ),
        (
            "personal2",
            r"/GenNIV/.+complete_personalcont\.aspx",
            r"[?&]node=Personal2(?:&|$)",
        ),
        (
            "travel",
            r"/GenNIV/.+complete_travel\.aspx",
            r"[?&]node=Travel(?:&|$)",
        ),
        (
            "travel_companions",
            r"/GenNIV/.+complete_travelcompanions\.aspx",
            r"[?&]node=TravelCompanions(?:&|$)",
        ),
        (
            "previous_us_travel",
            r"/GenNIV/.+complete_previousustravel\.aspx",
            r"[?&]node=PreviousUSTravel(?:&|$)",
        ),
        (
            "address_phone",
            r"/GenNIV/.+complete_contact\.aspx",
            r"[?&]node=AddressPhone(?:&|$)",
        ),
        (
            "passport",
            (
                r"/GenNIV/.+(?:complete_pptvisa|"
                r"passport_visa_info)\.aspx"
            ),
            r"[?&]node=PptVisa(?:&|$)",
        ),
        (
            "us_contact",
            r"/GenNIV/.+complete_uscontact\.aspx",
            r"[?&]node=USContact(?:&|$)",
        ),
        (
            "relatives",
            r"/GenNIV/.+complete_family1\.aspx",
            r"[?&]node=Relatives(?:&|$)",
        ),
        (
            "spouse",
            r"/GenNIV/.+complete_family2\.aspx",
            r"[?&]node=(?:Spouse|Family2)(?:&|$)",
        ),
        (
            "work_education1",
            r"/GenNIV/.+complete_workeducation1\.aspx",
            r"[?&]node=WorkEducation1(?:&|$)",
        ),
        (
            "work_education2",
            r"/GenNIV/.+complete_workeducation2\.aspx",
            r"[?&]node=WorkEducation2(?:&|$)",
        ),
        (
            "work_education3",
            r"/GenNIV/.+complete_workeducation3\.aspx",
            r"[?&]node=WorkEducation3(?:&|$)",
        ),
        (
            "sevis",
            r"/GenNIV/.+complete_sevis[^/]*\.aspx",
            r"[?&]node=SEVIS[^&]*(?:&|$)",
        ),
        (
            "additional_contacts",
            r"/GenNIV/.+additionalpointcontact[^/]*\.aspx",
            r"[?&]node=AdditionalPointContact[^&]*(?:&|$)",
        ),
        *tuple(
            (
                f"security_background{part}",
                (
                    rf"/GenNIV/.+complete_securityandbackground"
                    rf"{part}\.aspx"
                ),
                (
                    rf"[?&]node=SecurityandBackground"
                    rf"{part}(?:&|$)"
                ),
            )
            for part in range(1, 6)
        ),
    )

    def __init__(self, plans: Iterable[PagePlan], version=None):
        self.plans = tuple(plans)
        self.version = version or self.VERSION

    def match(self, observation) -> Optional[PagePlan]:
        if classify_ceac_page(observation).kind != "formal":
            return None
        matching = [
            plan for plan in self.plans
            if plan.matches(observation)
        ]
        if not matching:
            return None
        # Exact CEAC route/node plans own live execution. Broad legacy plans
        # remain readable only for old checkpoints and unusual deployments.
        return next(
            (
                plan for plan in matching
                if str(plan.id).startswith("ceac-plan-")
            ),
            matching[0],
        )

    def plan_by_id(self, page_plan_id) -> Optional[PagePlan]:
        candidate = str(page_plan_id or "")
        return next(
            (
                plan for plan in self.plans
                if str(plan.id) == candidate
            ),
            None,
        )

    @classmethod
    def resumable_ceac_stage_score(cls, url):
        """Rank exact safe DS-160 tabs by physical workflow progress."""
        try:
            parsed = urlsplit(str(url or ""))
        except ValueError:
            return 0
        if (
            parsed.scheme.casefold() != "https"
            or str(parsed.hostname or "").casefold() != "ceac.state.gov"
        ):
            return 0
        path = str(parsed.path or "")
        folded_path = path.casefold()
        if folded_path.startswith("/genniv/general/sign/"):
            return 500
        if folded_path.startswith("/genniv/general/review/"):
            return 400
        if folded_path.startswith("/genniv/general/photo/"):
            return 300
        if not folded_path.startswith("/genniv/general/complete/"):
            return 0
        searchable = f"{path}?{parsed.query}"
        for index, (_page_key, path_pattern, node_pattern) in enumerate(
            cls.CEAC_DYNAMIC_PAGE_ROUTES,
            start=1,
        ):
            if (
                re.search(path_pattern, path, flags=re.IGNORECASE)
                and re.search(
                    node_pattern,
                    searchable,
                    flags=re.IGNORECASE,
                )
            ):
                return 100 + index
        # Preserve an unknown formal page for inspection, but rank it behind
        # every explicitly owned form node.
        return 100

    @classmethod
    def is_resumable_ceac_url(cls, url):
        return cls.resumable_ceac_stage_score(url) > 0

    def equivalent_for_field(
        self,
        first_page_plan_id,
        second_page_plan_id,
        field_id,
    ):
        """Return whether two persisted IDs mean the same page for a field.

        Requiring both plans to own the field prevents the broad historical
        travel alias from incorrectly treating Travel and SEVIS as one page.
        """
        first_id = str(first_page_plan_id or "")
        second_id = str(second_page_plan_id or "")
        if not first_id or not second_id:
            return False
        first = self.plan_by_id(first_id)
        second = self.plan_by_id(second_id)
        if first is None or second is None:
            return False
        if not (
            first.allows_field(field_id)
            and second.allows_field(field_id)
        ):
            return False
        if first_id == second_id:
            return True
        return bool(
            second_id in self.LEGACY_PLAN_EQUIVALENTS.get(
                first_id, ()
            )
            or first_id in self.LEGACY_PLAN_EQUIVALENTS.get(
                second_id, ()
            )
        )

    def canonical_owner_for_field(self, field_id):
        """Return one stable physical-page owner across plan-version aliases."""
        candidate = str(field_id or "")
        if candidate.startswith("ceac."):
            parts = candidate.split(".", 2)
            if len(parts) >= 2:
                page_plan_id = self.CEAC_PAGE_CANONICAL_OWNERS.get(
                    parts[1],
                    f"ceac-plan-{parts[1]}",
                )
                plan = self.plan_by_id(page_plan_id)
                if (
                    plan is not None
                    and plan.allows_field(candidate)
                ):
                    return page_plan_id
        coarse_owner = self.COARSE_FIELD_OWNERS.get(candidate)
        if coarse_owner:
            return coarse_owner
        matching = [
            str(plan.id)
            for plan in self.plans
            if plan.allows_field(candidate)
        ]
        return matching[0] if len(matching) == 1 else ""

    @classmethod
    def terminal_reason(cls, observation):
        """Return the hard stop reason for Review/Sign/final-submit pages.

        This check deliberately ignores ordinary page body text because CEAC
        renders REVIEW and SIGN tabs on every form page, and its public
        instructions page also tells applicants that they must
        "electronically sign and submit" their own application.  Prose is not
        a navigation/control identity.  A terminal boundary must therefore be
        established by the current code-owned URL or page title only.
        """
        if classify_ceac_page(observation).kind in {
            "sign",
            "final_submit",
        }:
            return (
                "已到达 DS-160 Review/Sign 阶段；Gemini 已在最终签名和"
                "提交前停止，请人工核对后完成最终提交。"
            )
        return ""

    @classmethod
    def default(cls):
        # This list is intentionally explicit. Supporting a new CEAC page is a
        # code/configuration change, not a decision delegated to a model.
        def owned_by(page_plan_id):
            return frozenset(
                field_id
                for field_id, owner in cls.COARSE_FIELD_OWNERS.items()
                if owner == page_plan_id
            )

        personal_fields = owned_by("ceac-plan-personal1")
        personal2_fields = owned_by("ceac-plan-personal2")
        address_phone_fields = owned_by("ceac-plan-address_phone")
        passport_fields = owned_by("ceac-plan-passport")
        travel_fields = owned_by("ceac-plan-travel")
        us_contact_fields = owned_by("ceac-plan-us_contact")
        sevis_fields = owned_by("ceac-plan-sevis")
        previous_us_travel_fields = owned_by(
            "ceac-plan-previous_us_travel"
        )
        security_background1_fields = owned_by(
            "ceac-plan-security_background1"
        )
        security_background2_fields = owned_by(
            "ceac-plan-security_background2"
        )
        security_background4_fields = owned_by(
            "ceac-plan-security_background4"
        )
        plans = [
            PagePlan(
                id="personal-information",
                path_patterns=(r"/GenNIV/.+complete_personal\.aspx",),
                title_patterns=(r"personal information",),
                allowed_field_ids=personal_fields,
                allowed_field_prefixes=("ceac.personal1.",),
                required_field_ids=frozenset({
                    "personal.surname", "personal.givenNames"
                }),
                field_labels={
                    "personal.surname": ("Surnames", "Surname"),
                    "personal.givenNames": ("Given Names",),
                    "personal.dateOfBirth": ("Date of Birth",),
                    "personal.sex": ("Sex",),
                    "personal.nationality": (
                        "Country/Region of Origin (Nationality)",
                        "Nationality",
                    ),
                    "personal.placeOfBirth": (
                        "City of Birth", "Place of Birth",
                    ),
                },
                control_hints={
                    "personal.surname": ("APP_SURNAME",),
                    "personal.givenNames": ("APP_GIVEN_NAME",),
                    "personal.dateOfBirth": ("DOB",),
                    "personal.sex": ("APP_GENDER",),
                    "personal.nationality": ("APP_NATL",),
                    "personal.placeOfBirth": (
                        "APP_POB_CITY", "POB_CITY",
                    ),
                },
            ),
            PagePlan(
                id="passport-information",
                path_patterns=(r"/GenNIV/.+(passport|ppt).+\.aspx",),
                title_patterns=(r"passport|travel document",),
                allowed_field_ids=passport_fields,
                allowed_field_prefixes=("ceac.passport.",),
                required_field_ids=frozenset({"passport.number"}),
                field_labels={
                    "passport.number": (
                        "Passport/Travel Document Number",
                        "Passport Number",
                    ),
                    "passport.issuance": (
                        "Issuance Date", "Passport Issuance Date",
                    ),
                    "passport.expiration": (
                        "Expiration Date", "Passport Expiration Date",
                    ),
                    "passport.issuingCountry": (
                        "Country/Region Where Issued",
                        "Passport Issuing Country",
                    ),
                },
                control_hints={
                    "passport.number": (
                        "PPT_NUM", "PASSPORT_NUMBER",
                    ),
                    "passport.issuance": (
                        "PPT_ISSUED", "ISSUANCE_DATE",
                    ),
                    "passport.expiration": (
                        "PPT_EXPIRE", "EXPIRATION_DATE",
                    ),
                    "passport.issuingCountry": (
                        "PPT_ISSUED_CNTRY", "ISSUE_COUNTRY",
                    ),
                },
            ),
            PagePlan(
                id="travel-information",
                path_patterns=(r"/GenNIV/.+complete_travel\.aspx",),
                title_patterns=(r"travel",),
                allowed_field_ids=travel_fields,
                allowed_field_prefixes=("ceac.travel.",),
                field_labels={
                    "travel.purpose": (
                        "Purpose of Trip to the U.S.",
                        "Travel Purpose",
                    ),
                    "travel.arrivalDate": (
                        "Intended Date of Arrival", "Arrival Date",
                    ),
                },
                control_hints={
                    "travel.purpose": (
                        "TRAVEL_PURPOSE", "PURPOSE_OF_TRIP",
                    ),
                    "travel.arrivalDate": (
                        "ARRIVAL_DATE", "DT_ARRIVAL",
                    ),
                },
            ),
            PagePlan(
                id="sevis-information",
                path_patterns=(r"/GenNIV/.+(student|sevis).+\.aspx",),
                title_patterns=(r"student|sevis",),
                allowed_field_ids=sevis_fields,
                allowed_field_prefixes=("ceac.sevis.",),
                field_labels={
                    "education.schoolName": (
                        "Name of School", "School Name",
                    ),
                    "education.sevisId": ("SEVIS ID",),
                },
                control_hints={
                    "education.schoolName": (
                        "SCHOOL_NAME", "EDUCATION_SCHOOL",
                    ),
                    "education.sevisId": ("SEVIS", "SEVIS_ID"),
                },
            ),
        ]
        # The exact ``ceac-plan-*`` routes are the only plans selected during
        # live execution, while the original semantic aliases above still own
        # many of the battle-tested labels and ASP.NET control hints.  Keeping
        # those descriptors only on the legacy aliases silently discarded them
        # at runtime (for example CEAC's plural ``Surnames``/``APP_SURNAME``),
        # causing a visually correct Gemini coordinate to fail the independent
        # DOM identity gate.  Project every descriptor onto the field's
        # canonical physical owner before constructing the exact plans.  This
        # is field-owned rather than plan-pair-owned so descriptors such as
        # nationality correctly land on Personal 2 even though they were
        # historically stored in the broad Personal plan.
        dynamic_field_labels = {}
        dynamic_control_hints = {}

        def descriptor_owner(field_id):
            candidate = str(field_id or "")
            owner = cls.COARSE_FIELD_OWNERS.get(candidate)
            if owner:
                return owner
            if candidate.startswith("ceac."):
                parts = candidate.split(".", 2)
                if len(parts) >= 2:
                    return cls.CEAC_PAGE_CANONICAL_OWNERS.get(
                        parts[1],
                        f"ceac-plan-{parts[1]}",
                    )
            return ""

        def merge_descriptor(target, owner, field_id, values):
            if not owner:
                return
            owner_fields = target.setdefault(owner, {})
            merged = list(owner_fields.get(field_id, ()))
            for value in tuple(values or ()):
                normalized = str(value or "").strip()
                if normalized and normalized not in merged:
                    merged.append(normalized)
            if merged:
                owner_fields[field_id] = tuple(merged)

        for descriptor_plan in tuple(plans):
            for field_id, labels in descriptor_plan.field_labels.items():
                merge_descriptor(
                    dynamic_field_labels,
                    descriptor_owner(field_id),
                    field_id,
                    labels,
                )
            for field_id, hints in descriptor_plan.control_hints.items():
                merge_descriptor(
                    dynamic_control_hints,
                    descriptor_owner(field_id),
                    field_id,
                    hints,
                )

        dynamic_ceac_pages = cls.CEAC_DYNAMIC_PAGE_ROUTES
        dynamic_legacy_fields = {
            "personal1": personal_fields,
            "personal2": personal2_fields,
            "address_phone": address_phone_fields,
            "passport": passport_fields,
            "travel": travel_fields,
            "previous_us_travel": previous_us_travel_fields,
            "us_contact": us_contact_fields,
            "sevis": sevis_fields,
            "security_background1": security_background1_fields,
            "security_background2": security_background2_fields,
            "security_background4": security_background4_fields,
        }
        dynamic_required_fields = {
            "personal1": frozenset({
                "personal.surname",
                "personal.givenNames",
            }),
            "passport": frozenset({"passport.number"}),
        }
        plans.extend(
            PagePlan(
                id=f"ceac-plan-{page_key}",
                # CEAC's stable contract is its formal-form route plus node
                # query.  Page titles vary ("U.S. Contact" vs.
                # "U.S. Point of Contact") and tooltip language changes them,
                # so an exact route must not be rejected by display wording.
                path_patterns=(path_pattern, node_pattern),
                title_patterns=(),
                allowed_field_ids=dynamic_legacy_fields.get(
                    page_key,
                    frozenset(),
                ),
                allowed_field_prefixes=(f"ceac.{page_key}.",),
                required_field_ids=dynamic_required_fields.get(
                    page_key,
                    frozenset(),
                ),
                field_labels=dict(dynamic_field_labels.get(
                    f"ceac-plan-{page_key}",
                    {},
                )),
                control_hints=dict(dynamic_control_hints.get(
                    f"ceac-plan-{page_key}",
                    {},
                )),
            )
            for page_key, path_pattern, node_pattern in dynamic_ceac_pages
        )
        plans.append(
            PagePlan(
                id="ceac-plan-photo",
                path_patterns=(
                    r"/GenNIV/General/Photo/[^/?#]+\.aspx",
                    r"[?&]node=Photo[A-Za-z0-9_.-]*(?:&|$)",
                ),
                title_patterns=(),
                # No document path is exposed to the visual model. If CEAC
                # already has an accepted photo, the fixed Next control can
                # advance to Review; otherwise CEAC's own validation safely
                # stops the run without guessing or submitting anything.
                allowed_field_prefixes=("ceac.photo.",),
            )
        )
        return cls(plans)


def classify_ceac_page(observation):
    """Classify rendered CEAC state from route *and* live-page evidence.

    CEAC sometimes serves an expired-session document at the previous formal
    URL.  Negative boundary evidence therefore wins before route scoring, and
    a formal classification requires both a supported workflow URL and a
    structural page heading/control.  No model output participates here.
    """

    url = str(getattr(observation, "url", "") or "")
    title = str(getattr(observation, "title", "") or "")
    visible_text = str(
        getattr(observation, "visible_text", "") or ""
    )
    try:
        parsed = urlsplit(url)
    except ValueError:
        return CEACPageClassification("unsupported")
    if (
        parsed.scheme.casefold() != "https"
        or str(parsed.hostname or "").casefold() != "ceac.state.gov"
    ):
        return CEACPageClassification("unsupported")

    path = str(parsed.path or "")
    folded_path = path.casefold()
    rendered = f"{title}\n{visible_text}".casefold()
    searchable = f"{path}?{parsed.query}"
    stage_score = PagePlanRegistry.resumable_ceac_stage_score(url)

    if (
        folded_path.endswith("/genniv/common/sessiontimedout.aspx")
        or re.search(
            r"\bsession\s+(?:(?:has|is)\s+)?(?:timed\s*out|expired)\b",
            rendered,
        )
        or re.search(r"会话.{0,12}(?:超时|过期|失效)", rendered)
    ):
        return CEACPageClassification(
            "session_timeout",
            stage_score=stage_score,
            reason="ceac_session_expired",
        )

    if re.search(
        r"\bcaptcha\b|\bi(?:'m| am) not a robot\b|\bsecurity check\b",
        rendered,
    ):
        return CEACPageClassification(
            "captcha",
            stage_score=stage_score,
            reason="captcha_visible",
        )

    if (
        folded_path.startswith("/genniv/general/sign/")
        or re.search(r"[?&]node=Sign[A-Za-z0-9_.-]*(?:&|$)", searchable,
                     flags=re.IGNORECASE)
        or re.search(
            r"\bsign\s+and\s+submit\b|\belectronic\s+signature\b",
            title,
            flags=re.IGNORECASE,
        )
    ):
        return CEACPageClassification(
            "sign",
            stage_score=max(stage_score, 500),
            reason="signature_boundary",
        )

    if (
        folded_path.startswith("/genniv/general/review/")
        or re.search(r"[?&]node=Review[A-Za-z0-9_.-]*(?:&|$)", searchable,
                     flags=re.IGNORECASE)
        or re.search(
            r"\breview\s+(?:your\s+)?(?:application|information)\b"
            r"|\bfinal\s+submit\b|\bsubmit\s+application\b",
            title,
            flags=re.IGNORECASE,
        )
        or any(
            re.search(pattern, url, flags=re.IGNORECASE)
            for pattern in PagePlanRegistry.TERMINAL_URL_PATTERNS
        )
        or any(
            re.search(pattern, title, flags=re.IGNORECASE)
            for pattern in PagePlanRegistry.TERMINAL_TITLE_PATTERNS
        )
    ):
        return CEACPageClassification(
            "final_submit",
            stage_score=max(stage_score, 400),
            reason="review_or_submit_boundary",
        )

    if folded_path in {
        "/genniv/default.aspx",
        "/genniv/",
        "/genniv",
    }:
        return CEACPageClassification("default", reason="ceac_landing")

    if (
        folded_path.endswith("/genniv/common/recovery.aspx")
        or re.search(
            r"\b(?:recover|retrieve)\s+(?:your\s+|an?\s+)?application\b",
            rendered,
        )
    ):
        return CEACPageClassification(
            "recovery",
            reason="application_retrieval",
        )

    if not stage_score:
        return CEACPageClassification("unsupported")

    try:
        control_count = max(
            0,
            int(getattr(observation, "form_control_count", 0) or 0),
        )
    except (TypeError, ValueError):
        control_count = 0
    has_bound_controls = bool(
        dict(getattr(observation, "control_values", {}) or {})
        or dict(getattr(observation, "repeater_counts", {}) or {})
    )
    structural_title_patterns = (
        r"\bpersonal\s+information\b",
        r"\btravel(?:\s+companions?|\s+information)?\b",
        r"\bprevious\s+u\.s\.\s+travel\b",
        r"\baddress\s+and\s+phone\b",
        r"\bpassport(?:\s+information)?\b",
        r"\bu\.s\.\s+(?:point\s+of\s+)?contact\b",
        r"\bfamily\s+information\b",
        r"\bwork\W+education\W+(?:and\W+)?training\b",
        r"\bsecurity\s+and\s+background\b",
        r"\bstudent\W+(?:and\W+)?exchange\W+visitor\b",
        r"\bphoto\b",
    )
    has_structural_title = any(
        re.search(pattern, title, flags=re.IGNORECASE)
        for pattern in structural_title_patterns
    )
    if control_count or has_bound_controls or has_structural_title:
        return CEACPageClassification(
            "formal",
            stage_score=stage_score,
            reason="formal_route_and_structure",
        )
    return CEACPageClassification(
        "unsupported",
        stage_score=stage_score,
        reason="formal_route_without_live_form_structure",
    )
