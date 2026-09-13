#!/usr/bin/env python3
"""Recover ONE booking that was paid for but cancelled, and notify all three parties.

Why this exists: on 16 Aug 2026 the live Razorpay webhook had no Secret, so `payment.captured` was
rejected, the payment hold lapsed, and expire_stale_holds cancelled a booking Razorpay had already
taken money for. The customer paid and lost her session.

Fixing that by hand means touching bookings, customer_payments and mentor_payouts in the right order,
and the confirmation email will not fire on its own: it only sends from the payment flow, and the
verify endpoint deliberately stays silent on an already-confirmed booking so a repeated poll cannot
double-send. This does both, for exactly one booking.

Deliberately narrow:
  * one booking id, passed explicitly; nothing is scanned or bulk-updated
  * all database work in ONE transaction, rolled back on any problem
  * refuses unless the payment is genuinely captured at Razorpay, so it cannot confirm an unpaid
    booking on someone's word
  * refuses if the slot has passed
  * the slot-overlap exclusion constraint is left to do its job: if another booking took that time
    while this one was cancelled, the transaction fails and nothing changes

Uses the real confirm_booking_payment RPC rather than hand-written UPDATEs, so the payout row and
payment state end up exactly as a normal payment would leave them.

Usage:
    python -m scripts.recover_paid_booking <booking_id> <razorpay_payment_id> --dry-run
    python -m scripts.recover_paid_booking <booking_id> <razorpay_payment_id>
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("recover")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("booking_id")
    ap.add_argument("payment_id", help="Razorpay payment id, e.g. pay_TQT0UQ19d3OHBs")
    ap.add_argument("--dry-run", action="store_true", help="check everything, change nothing")
    ap.add_argument("--skip-razorpay-check", action="store_true",
                    help="do not verify the payment at Razorpay first. Only for a payment you have "
                         "already confirmed captured in the dashboard.")
    ap.add_argument("--email-only", action="store_true",
                    help="change nothing in the database, just send the three emails. For a booking "
                         "already restored by hand (e.g. in the SQL editor).")
    args = ap.parse_args()

    import config
    import db
    from routers.booking import _send_booking_confirmation
    from services import access_token

    dsn = config.SUPABASE_DB_URL
    if not dsn:
        log.error("no database URL configured")
        return 1

    # 1. Confirm with Razorpay that the money really moved. Without this the script would happily
    #    confirm an unpaid booking, which is the opposite of the problem it exists to fix.
    if not args.skip_razorpay_check and not args.email_only:
        try:
            rp = db.fetch_razorpay_payment(args.payment_id)
            status = (rp or {}).get("status")
            log.info("razorpay   : %s status=%s amount=%s",
                     args.payment_id, status, (rp or {}).get("amount"))
            if status != "captured":
                log.error("payment is %r at Razorpay, not 'captured'. Refusing.", status)
                return 1
        except Exception as e:
            log.error("could not verify the payment at Razorpay: %s", e)
            log.error("re-run with --skip-razorpay-check only if you have confirmed it in the dashboard.")
            return 1

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""select status, slot_time, slot_end, mentor_id
                       from bookings where id = %s""", (args.booking_id,))
        row = cur.fetchone()
        if not row:
            log.error("booking %s not found", args.booking_id)
            return 1
        status, slot_time, slot_end, _mentor = row
        log.info("booking    : %s", args.booking_id)
        log.info("status     : %s", status)
        log.info("slot       : %s -> %s", slot_time, slot_end)

        # A booking already restored by hand needs no database work, only the emails, which is the one
        # part SQL cannot do. Falls through rather than returning: sending is the remaining job.
        already_ok = status in ("confirmed", "rescheduled")
        if already_ok:
            log.info("already %s: no database work needed, emails only.", status)
        if slot_time is None or slot_time <= datetime.now(timezone.utc):
            log.error("slot is in the past. Refund instead of restoring.")
            return 1

        if args.dry_run:
            log.info("\nDRY RUN. Would %s, then email the customer, mentor and admins.",
                     "leave the booking exactly as it is" if already_ok or args.email_only
                     else f"restore this booking to confirmed against {args.payment_id}")
            # Preview the recipients and the actual link, because both come from environment the
            # caller had to set by hand: a stale FRONTEND_URL sends a real customer a dead button,
            # and a mismatched SUPABASE_JWT_SECRET makes the token 403 when she clicks it.
            preview = db.get_booking_notify_info(args.booking_id) or {}
            log.info("would email: %s (customer), %s (mentor), plus admins",
                     preview.get("candidate_email") or "(NONE - no email would be sent)",
                     preview.get("mentor_email") or "(none)")
            log.info("join link  : %s", access_token.session_url(args.booking_id, "candidate"))
            log.info("\nCheck that link points at production before re-running without --dry-run.")
            return 0

        if not (already_ok or args.email_only):
            # 2. Put it back into the state confirm_booking_payment requires (pending + a live hold),
            #    then let the real function do the work. A short hold is enough: it is cleared at once.
            cur.execute("""update bookings
                              set status = 'pending',
                                  payment_hold_expires_at = now() + interval '10 minutes'
                            where id = %s""", (args.booking_id,))
            cur.execute("""update customer_payments set state = 'created'
                            where booking_id = %s""", (args.booking_id,))
            cur.execute("select confirm_booking_payment(%s, %s)", (args.booking_id, args.payment_id))
            result = cur.fetchone()[0]
            log.info("confirm    : %s", result)

            # payout_state deliberately untouched: setting the booking back to 'pending' above fires
            # trg_sync_payout_state, which re-derives it. Writing it here would reintroduce the very
            # second-writer problem that made this recovery necessary. It is asserted below instead.
            cur.execute("""select b.status, p.state, p.provider_payment_id, po.payout_state
                           from bookings b
                           left join customer_payments p on p.booking_id = b.id
                           left join mentor_payouts po on po.booking_id = b.id
                           where b.id = %s""", (args.booking_id,))
            after = cur.fetchone()
            log.info("after      : status=%s payment=%s ref=%s payout=%s", *after)

            if after[0] != "confirmed":
                conn.rollback()
                log.error("booking did not reach 'confirmed'. Rolled back, nothing changed.")
                return 1
            conn.commit()
            log.info("committed.")

    # 3. Emails, after the commit: the customer, the mentor and the admins get the same messages a
    #    normal payment would have produced. Each recipient is sent independently, so one failure does
    #    not silence the others.
    info = db.get_booking_notify_info(args.booking_id) or {}
    to = info.get("candidate_email")
    if not to:
        log.error("confirmed, but no candidate_email on the booking, so no email sent.")
        return 1
    log.info("emailing   : %s (customer), %s (mentor), plus admins",
             to, info.get("mentor_email") or "(none)")
    _send_booking_confirmation(args.booking_id, "", to, info.get("candidate_name"))
    log.info("done. check Resend's dashboard for delivery status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
