"""Tests for the company business cockpit.

The business plan is owner-editable data stored inside the company document.
These tests pin the normalisation, the derived numbers and the update path used
by the Business view and the assistant context.
"""
from __future__ import annotations

import pytest

from codx.junior.company.models import BusinessPlan, Company, PipelineDeal


def test_company_business_defaults_are_empty_and_safe():
    company = Company(name="Acme")

    assert company.business.currency == "EUR"
    assert company.business.period == "month"
    assert company.business.revenue_target == 0
    assert company.business.pipeline_value == 0
    assert company.business.weighted_pipeline_value == 0
    assert company.business.revenue_progress == 0
    assert company.business.gross_profit == 0


def test_pipeline_and_revenue_derive_the_numbers_the_ui_shows():
    plan = BusinessPlan(
        currency="eur",
        revenue_target=100_000,
        revenue_actual=25_000,
        costs_actual=10_000,
        pipeline=[
            {"name": "Northwind", "value": 20_000, "stage": "proposal", "probability": 50},
            {"name": "Lumen", "value": 10_000, "stage": "won", "probability": 100},
            {"name": "Lost deal", "value": 5_000, "stage": "lost", "probability": 0},
        ],
    )

    assert plan.currency == "EUR"
    assert plan.pipeline_value == 20_000
    assert plan.weighted_pipeline_value == 10_000
    assert plan.won_value == 10_000
    assert plan.gross_profit == 15_000
    assert plan.revenue_progress == 25
    assert plan.revenue_gap == 75_000


def test_business_plan_clamps_probability_and_rejects_unknown_stage():
    with pytest.raises(ValueError):
        PipelineDeal(name="Bad", stage="maybe")

    plan = BusinessPlan(pipeline=[{"name": "Big", "value": 100, "probability": 220}])
    assert plan.pipeline[0].probability == 100
    assert plan.pipeline[0].weighted_value == 100


def test_company_update_assignment_rebuilds_the_business_plan():
    company = Company(name="Acme")
    company.business = {
        "currency": "usd",
        "revenue_target": 5_000,
        "revenue_actual": 1_000,
        "pipeline": [{"name": "Deal", "value": 2_000, "stage": "negotiation"}],
    }

    assert isinstance(company.business, BusinessPlan)
    assert company.business.currency == "USD"
    assert company.business.revenue_progress == 20
    assert company.summary()["business"]["pipeline_value"] == 2_000
