"""initial schema: encounters, draft_notes, summary_requests, audit_events

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- encounters (FHIR Encounter mirror; id IS the FHIR Encounter id) ---
    op.create_table(
        "encounters",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("patient_id", sa.String(), nullable=False),
        sa.Column("attending_practitioner_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_encounters"),
    )
    op.create_index("ix_encounters_patient_id", "encounters", ["patient_id"])
    op.create_index(
        "ix_encounters_attending_practitioner_id",
        "encounters",
        ["attending_practitioner_id"],
    )

    # --- draft_notes ---
    op.create_table(
        "draft_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("encounter_id", sa.String(), nullable=False),
        # Allowed values (SPEC.md §2, §6): drafted | drafted_flagged |
        # drafted_unverified | edited | signed | failed
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("draft_text", postgresql.JSONB(), nullable=True),
        sa.Column("edited_text", postgresql.JSONB(), nullable=True),
        sa.Column("diff", postgresql.JSONB(), nullable=True),
        sa.Column("prompt_template_version", sa.String(), nullable=True),
        sa.Column("claude_model", sa.String(), nullable=True),
        sa.Column("grok_review", postgresql.JSONB(), nullable=True),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_draft_notes"),
        sa.CheckConstraint(
            "status IN ('drafted', 'drafted_flagged', 'drafted_unverified', "
            "'edited', 'signed', 'failed')",
            name="ck_draft_notes_status",
        ),
    )
    op.create_index("ix_draft_notes_encounter_id", "draft_notes", ["encounter_id"])

    # --- summary_requests ---
    op.create_table(
        "summary_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", sa.String(), nullable=False),
        sa.Column("summary_text", postgresql.JSONB(), nullable=True),
        # Allowed values (SPEC.md §2, §6): clean | flagged | unverified
        sa.Column("review_status", sa.String(), nullable=False),
        sa.Column("prompt_template_version", sa.String(), nullable=True),
        sa.Column("claude_model", sa.String(), nullable=True),
        sa.Column("grok_review", postgresql.JSONB(), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_summary_requests"),
        sa.CheckConstraint(
            "review_status IN ('clean', 'flagged', 'unverified')",
            name="ck_summary_requests_review_status",
        ),
    )
    op.create_index("ix_summary_requests_patient_id", "summary_requests", ["patient_id"])

    # --- audit_events ---
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=False),
        # Allowed values (SPEC.md §2): success | failure
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure')",
            name="ck_audit_events_outcome",
        ),
    )


def downgrade() -> None:
    op.drop_table("audit_events")

    op.drop_index("ix_summary_requests_patient_id", table_name="summary_requests")
    op.drop_table("summary_requests")

    op.drop_index("ix_draft_notes_encounter_id", table_name="draft_notes")
    op.drop_table("draft_notes")

    op.drop_index("ix_encounters_attending_practitioner_id", table_name="encounters")
    op.drop_index("ix_encounters_patient_id", table_name="encounters")
    op.drop_table("encounters")
