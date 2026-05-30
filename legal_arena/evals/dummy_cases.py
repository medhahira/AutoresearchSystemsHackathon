from __future__ import annotations

from pydantic import BaseModel, Field

from legal_arena.schemas import CaseSchema, Side, SynthesizedSources


class TurnSources(BaseModel):
    round_number: int = Field(ge=1)
    side: Side
    question: str
    sources: SynthesizedSources


class DummyCase(BaseModel):
    case_id: str
    case: CaseSchema
    documents: list[str]
    turn_sources: list[TurnSources]
    expected_themes: list[str]
    expected_risk_band: tuple[int, int]


DUMMY_CASES: list[DummyCase] = [
    DummyCase(
        case_id="tenant-lockout-ca",
        case=CaseSchema(
            title="Tenant Lockout After Repair Dispute",
            summary="A California tenant alleges the landlord changed locks after the tenant withheld rent over unresolved habitability defects. The landlord claims abandonment and unpaid rent.",
            facts=[
                "Tenant sent written repair notices about mold and heat outages.",
                "Landlord changed the unit lock while tenant property remained inside.",
                "Tenant had withheld one month of rent before the lockout.",
            ],
            prosecution_must_prove=["Tenant breached lease", "Unit was abandoned", "Rent remains unpaid"],
            defense_must_prove=["Lockout was unlawful", "Habitability defects justified withholding", "Tenant did not abandon possession"],
            charges_or_claims=["Wrongful eviction", "Breach of lease", "Habitability violation"],
            penalties_at_stake=["Statutory damages", "Possession restoration", "Rent offset"],
            relevant_jurisdictions=["California"],
            optimise_for="defense",
        ),
        documents=[
            "Email thread: tenant reported mold and heat outage on January 8 and January 17. Photos attached. Landlord replied that repairs would wait until next month.",
            "Move-out inspection note: landlord found furniture, medication, and work equipment in the unit when locks were changed.",
        ],
        turn_sources=[
            TurnSources(
                round_number=1,
                side="prosecution",
                question="What supports unpaid rent and abandonment?",
                sources=SynthesizedSources(
                    relevant_excerpts="Lease requires monthly rent by the first. Payment ledger shows one missed month. No written surrender appears in the record.",
                    key_precedents=["Mock Lease Ledger v. Possession"],
                    supporting_statutes=["Cal. Civ. Code lease payment obligations"],
                    gaps=["No direct abandonment notice found"],
                    citations=["lease-ledger-tenant-lockout"],
                    confidence=0.72,
                ),
            ),
            TurnSources(
                round_number=1,
                side="defense",
                question="What supports unlawful lockout and habitability defense?",
                sources=SynthesizedSources(
                    relevant_excerpts="California tenant protections generally prohibit self-help lockouts and preserve remedies for serious habitability defects after notice.",
                    key_precedents=["Mock Tenant v. Self Help Lockout"],
                    supporting_statutes=["Cal. Civ. Code tenant lockout protections"],
                    gaps=[],
                    citations=["tenant-email-repair-notices", "unit-property-inventory"],
                    confidence=0.86,
                ),
            ),
        ],
        expected_themes=["self-help lockout", "habitability", "no abandonment"],
        expected_risk_band=(3, 6),
    ),
    DummyCase(
        case_id="wage-theft-ny",
        case=CaseSchema(
            title="Restaurant Overtime and Tip Pool Dispute",
            summary="A New York restaurant worker claims unpaid overtime and an invalid tip pool that included managers. The restaurant argues all tips were voluntary and hours are overstated.",
            facts=[
                "Employee clock records show recurring 52-hour weeks.",
                "Tip pool spreadsheet includes shift leads with hiring authority.",
                "Employer did not provide signed wage notices for two quarters.",
            ],
            prosecution_must_prove=["Hours worked exceeded 40", "Overtime premium was unpaid", "Tip pool included ineligible managers"],
            defense_must_prove=["Records are inaccurate", "Shift leads were not managers", "Any underpayment was corrected"],
            charges_or_claims=["FLSA overtime", "New York Labor Law wage notice", "Improper tip pooling"],
            penalties_at_stake=["Back wages", "Liquidated damages", "Statutory notice penalties"],
            relevant_jurisdictions=["New York", "Federal - 2nd Circuit"],
            optimise_for="prosecution",
        ),
        documents=[
            "Payroll export: worker clocked 48 to 55 hours in six of eight sampled weeks with no overtime premium line item.",
            "Tip pool rules: shift leads approve schedule swaps and discipline servers. Spreadsheet includes two shift leads in weekly distribution.",
        ],
        turn_sources=[
            TurnSources(
                round_number=1,
                side="prosecution",
                question="What supports overtime and invalid tip pool liability?",
                sources=SynthesizedSources(
                    relevant_excerpts="FLSA overtime requires premium pay over 40 hours. Tip credits are vulnerable where managers participate in pooled tips.",
                    key_precedents=["Mock Server v. Manager Tip Pool"],
                    supporting_statutes=["29 U.S.C. overtime rule", "NYLL wage notice requirements"],
                    gaps=[],
                    citations=["payroll-export", "tip-pool-spreadsheet"],
                    confidence=0.88,
                ),
            ),
            TurnSources(
                round_number=1,
                side="defense",
                question="What supports employer defenses?",
                sources=SynthesizedSources(
                    relevant_excerpts="Employer can challenge employee estimates with reliable time records, but current payroll data appears to corroborate overtime hours.",
                    key_precedents=["Mock Cafe v. Reconstructed Hours"],
                    supporting_statutes=[],
                    gaps=["No corrected payment records found"],
                    citations=["time-record-sample"],
                    confidence=0.67,
                ),
            ),
        ],
        expected_themes=["overtime", "tip pool", "wage notices"],
        expected_risk_band=(2, 5),
    ),
    DummyCase(
        case_id="dui-stop-fl",
        case=CaseSchema(
            title="DUI Stop With Bodycam Gap",
            summary="A Florida driver faces DUI charges after a traffic stop where bodycam footage has a missing segment before field sobriety tests. The state relies on officer observations and breath results.",
            facts=[
                "Officer cited weaving within lane and delayed braking.",
                "Bodycam has a four-minute gap before field sobriety tests.",
                "Breath test was administered 95 minutes after the stop.",
            ],
            prosecution_must_prove=["Lawful stop", "Impairment", "Reliable breath test"],
            defense_must_prove=["Reasonable suspicion was weak", "Video gap undermines observations", "Breath test reliability is contestable"],
            charges_or_claims=["DUI"],
            penalties_at_stake=["License suspension", "Fines", "Probation", "Possible jail exposure"],
            relevant_jurisdictions=["Florida"],
            optimise_for="defense",
        ),
        documents=[
            "Police report: odor of alcohol, watery eyes, and admission of two drinks. Report notes camera battery warning.",
            "Breath log: calibration passed earlier that week. Test completed 95 minutes after stop; result 0.082.",
        ],
        turn_sources=[
            TurnSources(
                round_number=1,
                side="prosecution",
                question="What supports admissibility and impairment?",
                sources=SynthesizedSources(
                    relevant_excerpts="Officer observations plus a breath result over the limit can establish impairment if stop and testing foundation are accepted.",
                    key_precedents=["Mock State v. Late Breath Test"],
                    supporting_statutes=["Florida DUI statute"],
                    gaps=["Missing bodycam segment unexplained"],
                    citations=["police-report", "breath-log"],
                    confidence=0.74,
                ),
            ),
            TurnSources(
                round_number=1,
                side="defense",
                question="What supports suppression or reasonable doubt?",
                sources=SynthesizedSources(
                    relevant_excerpts="A bodycam gap before sobriety tests gives defense a credibility attack, especially where breath result is borderline and delayed.",
                    key_precedents=["Mock Driver v. Video Gap"],
                    supporting_statutes=[],
                    gaps=["Need maintenance logs and officer discipline history"],
                    citations=["bodycam-metadata", "breath-log"],
                    confidence=0.78,
                ),
            ),
        ],
        expected_themes=["bodycam gap", "borderline breath", "reasonable suspicion"],
        expected_risk_band=(4, 7),
    ),
    DummyCase(
        case_id="data-breach-il",
        case=CaseSchema(
            title="Clinic Data Breach Notice Delay",
            summary="Patients allege an Illinois clinic waited too long to notify them after a ransomware incident exposed billing records. The clinic argues forensic confirmation took weeks.",
            facts=[
                "Ransomware alert occurred March 3.",
                "Forensic report confirmed exfiltration April 16.",
                "Patient notices were mailed June 12.",
            ],
            prosecution_must_prove=["Protected data was exposed", "Notice was unreasonably delayed", "Patients suffered cognizable harm"],
            defense_must_prove=["Delay was tied to forensic confirmation", "Notice complied with statute", "Damages are speculative"],
            charges_or_claims=["State data breach notice claim", "Negligence", "Consumer protection claim"],
            penalties_at_stake=["Statutory penalties", "Notification costs", "Class settlement exposure"],
            relevant_jurisdictions=["Illinois"],
            optimise_for="defense",
        ),
        documents=[
            "Incident timeline: alert March 3, containment March 5, outside forensics retained March 7, exfiltration confirmed April 16.",
            "Notice packet: mailed June 12 with credit monitoring offer. Draft was approved by counsel May 29.",
        ],
        turn_sources=[
            TurnSources(
                round_number=1,
                side="prosecution",
                question="What supports unreasonable delay?",
                sources=SynthesizedSources(
                    relevant_excerpts="Plaintiffs can argue notice lagged nearly three months from initial detection and two weeks after counsel approved the notice packet.",
                    key_precedents=["Mock Patient v. Delayed Notice"],
                    supporting_statutes=["Illinois personal information breach notice law"],
                    gaps=["Need exact statutory safe-harbor language"],
                    citations=["incident-timeline", "notice-packet"],
                    confidence=0.7,
                ),
            ),
            TurnSources(
                round_number=1,
                side="defense",
                question="What supports compliance and low damages?",
                sources=SynthesizedSources(
                    relevant_excerpts="Defense can frame the clock as starting at forensic confirmation, not first alert, and emphasize credit monitoring and absence of confirmed misuse.",
                    key_precedents=["Mock Clinic v. Forensic Confirmation"],
                    supporting_statutes=["Illinois breach notice timing standard"],
                    gaps=["Need evidence of no misuse"],
                    citations=["forensic-report", "notice-packet"],
                    confidence=0.76,
                ),
            ),
        ],
        expected_themes=["forensic confirmation", "notice delay", "damages"],
        expected_risk_band=(4, 7),
    ),
    DummyCase(
        case_id="noncompete-wa",
        case=CaseSchema(
            title="Software Engineer Noncompete Threat",
            summary="A Washington software engineer received a cease-and-desist after joining a competitor. The former employer invokes a noncompete and confidentiality clause.",
            facts=[
                "Engineer earned below the statutory compensation threshold when agreement was signed.",
                "New role uses general backend engineering skills.",
                "Former employer identified no specific files taken.",
            ],
            prosecution_must_prove=["Agreement is enforceable", "New role violates scope", "Confidential information is at risk"],
            defense_must_prove=["Noncompete is void or overbroad", "No trade secret misappropriation", "Role uses general skills"],
            charges_or_claims=["Noncompete enforcement", "Breach of confidentiality", "Trade secret threat"],
            penalties_at_stake=["Injunction", "Attorney fees", "Employment disruption"],
            relevant_jurisdictions=["Washington"],
            optimise_for="defense",
        ),
        documents=[
            "Offer letter: salary at signing was below current Washington noncompete threshold. Agreement barred work for any cloud software competitor for 18 months.",
            "Cease-and-desist: alleges inevitable disclosure but cites no downloaded repository, customer list, or architecture document.",
        ],
        turn_sources=[
            TurnSources(
                round_number=1,
                side="prosecution",
                question="What supports injunction risk?",
                sources=SynthesizedSources(
                    relevant_excerpts="Employer can rely on confidentiality obligations and close competitive overlap, but noncompete enforceability is uncertain under Washington limits.",
                    key_precedents=["Mock CloudCo v. Engineer"],
                    supporting_statutes=["Washington noncompete statute"],
                    gaps=["No concrete trade secret evidence found"],
                    citations=["cease-and-desist"],
                    confidence=0.63,
                ),
            ),
            TurnSources(
                round_number=1,
                side="defense",
                question="What supports employee defense?",
                sources=SynthesizedSources(
                    relevant_excerpts="Washington restrictions on noncompetes and compensation thresholds strongly support challenging the agreement, especially absent evidence of file taking.",
                    key_precedents=["Mock Engineer v. Overbroad Noncompete"],
                    supporting_statutes=["Washington noncompete compensation threshold"],
                    gaps=[],
                    citations=["offer-letter", "cease-and-desist"],
                    confidence=0.9,
                ),
            ),
        ],
        expected_themes=["threshold", "overbroad", "no trade secrets"],
        expected_risk_band=(2, 5),
    ),
]