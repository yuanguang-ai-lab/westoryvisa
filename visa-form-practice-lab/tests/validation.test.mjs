import test from "node:test";
import assert from "node:assert/strict";

import { ALL_FIELDS, STEPS, fieldById, stepIndexById } from "../src/config.ts";
import { EXAMPLE_DATA } from "../src/example.ts";
import {
  cleanHiddenValues,
  generatePracticeNumber,
  isSuspectedRealEmail,
  isVisible,
  overallCompletion,
  validateField,
  validateStep
} from "../src/validation.ts";

test("field configuration has unique stable identifiers", () => {
  const ids = ALL_FIELDS.map((field) => field.id);
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(STEPS.length, 16);
});

test("reserved example email passes and common real domains are rejected", () => {
  assert.equal(isSuspectedRealEmail("alex@example.com"), false);
  assert.equal(isSuspectedRealEmail("trainer@example.org"), false);
  assert.equal(isSuspectedRealEmail("person@gmail.com"), true);
  assert.equal(isSuspectedRealEmail("person@qq.com"), true);
});

test("sensitive fictional identifiers require DEMO prefixes", () => {
  const passport = fieldById("passportNumber");
  const nationalId = fieldById("nationalId");
  assert.ok(passport);
  assert.ok(nationalId);
  assert.equal(validateField(passport, { passportNumber: "DEMO123456" }, "en"), null);
  assert.match(validateField(passport, { passportNumber: "E12345678" }, "en") || "", /DEMO/);
  assert.equal(validateField(nationalId, { nationalId: "DEMO-ID-2026" }, "en"), null);
});

test("conditional fields appear only after their parent answer", () => {
  const otherSurname = fieldById("otherSurname");
  assert.ok(otherSurname);
  assert.equal(isVisible(otherSurname, { usedOtherNames: "no" }), false);
  assert.equal(isVisible(otherSurname, { usedOtherNames: "yes" }), true);
});

test("hidden conditional values are removed to prevent ghost data", () => {
  const cleaned = cleanHiddenValues({
    usedOtherNames: "no",
    otherSurname: "Oldexample",
    otherGivenName: "Alex",
    specificPlans: "no",
    arrivalFlight: "DEMO100"
  });
  assert.equal(Object.hasOwn(cleaned, "otherSurname"), false);
  assert.equal(Object.hasOwn(cleaned, "otherGivenName"), false);
  assert.equal(Object.hasOwn(cleaned, "arrivalFlight"), false);
});

test("travel chronology prevents past arrivals and backwards departures", () => {
  const step = stepIndexById("travel");
  const errors = validateStep(step, {
    tripPurpose: "EDUCATIONAL_PRACTICE",
    arrivalDate: "2025-01-01",
    stayLength: "10 DAYS",
    tripAddress: "100 Example Avenue",
    tripPayer: "SELF",
    specificPlans: "yes",
    arrivalFlight: "DEMO100",
    arrivalCity: "Sample City",
    departureCity: "Example City",
    departureDate: "2024-12-30"
  }, "en", new Date("2026-07-14T12:00:00Z"));
  assert.match(errors.arrivalDate, /past/i);
  assert.match(errors.departureDate, /earlier/i);
});

test("passport expiration must follow issuance", () => {
  const step = stepIndexById("passport");
  const errors = validateStep(step, {
    passportType: "regular",
    passportNumber: "DEMO123456",
    passportAuthority: "Exampleland",
    passportIssueCity: "Sample City",
    passportIssueCountry: "Exampleland",
    passportIssueDate: "2027-01-01",
    passportExpiration: "2026-01-01",
    lostPassport: "no"
  }, "en");
  assert.match(errors.passportExpiration, /later/i);
});

test("dynamic histories validate every record and chronology", () => {
  const education = fieldById("educationHistory");
  assert.ok(education);
  const message = validateField(education, {
    educationHistory: [{ school: "Example University", subject: "Practice", start: "2020-01-01", end: "2019-01-01" }]
  }, "en");
  assert.match(message || "", /End date/);
});

test("the complete fictional example produces a high practice completion score", () => {
  assert.ok(overallCompletion(EXAMPLE_DATA, "en") >= 90);
});

test("practice numbers are visibly unofficial", () => {
  const number = generatePracticeNumber(new Date("2026-07-14T00:00:00Z"), 0.5);
  assert.match(number, /^PRACTICE-2026-[A-Z0-9]{4}$/);
});
