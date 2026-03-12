import enum


class BusinessLine(str, enum.Enum):
    car = "car"
    golf_cart = "golf_cart"


class UnitType(str, enum.Enum):
    car = "car"
    golf_cart = "golf_cart"


class UnitStatus(str, enum.Enum):
    acquired = "acquired"
    in_inspection = "in_inspection"
    in_repair = "in_repair"
    waiting_parts = "waiting_parts"
    frontline_ready = "frontline_ready"
    sold = "sold"
    leased_rto_active = "leased_rto_active"
    closed = "closed"
    returned_special_review = "returned_special_review"


class CustomerStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"


class RepairJobType(str, enum.Enum):
    internal_recon = "internal_recon"
    customer_repair = "customer_repair"
    customer_support_repair = "customer_support_repair"


class RepairJobStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    waiting_parts = "waiting_parts"
    complete = "complete"
    cancelled = "cancelled"


class SaleStatus(str, enum.Enum):
    pending = "pending"
    complete = "complete"
    cancelled = "cancelled"


class LeaseStatus(str, enum.Enum):
    active = "active"
    paid_off = "paid_off"
    defaulted = "defaulted"
    cancelled = "cancelled"


class DelinquencyStatus(str, enum.Enum):
    current = "current"
    late = "late"
    delinquent = "delinquent"
    default = "default"


class PaymentFrequency(str, enum.Enum):
    weekly = "weekly"
    bi_weekly = "bi_weekly"
    monthly = "monthly"


class PaymentMethod(str, enum.Enum):
    cash = "cash"
    check = "check"
    card = "card"
    transfer = "transfer"
    other = "other"


class TransactionType(str, enum.Enum):
    purchase = "purchase"
    sale = "sale"
    charge = "charge"
    collection = "collection"
    repair_revenue = "repair_revenue"
    parts_revenue = "parts_revenue"
    labor_cost = "labor_cost"
    materials_cost = "materials_cost"
    overhead = "overhead"
    adjustment = "adjustment"


class RevenueStream(str, enum.Enum):
    golf_cart_sale = "golf_cart_sale"
    golf_cart_repair = "golf_cart_repair"
    golf_cart_parts_sale = "golf_cart_parts_sale"
    car_sale = "car_sale"
    car_rto_lease = "car_rto_lease"
    car_repair = "car_repair"


class ReviewStatus(str, enum.Enum):
    pending = "pending"
    reviewed = "reviewed"
    approved = "approved"


class ExceptionTxnStatus(str, enum.Enum):
    none = "none"
    flagged = "flagged"
    resolved = "resolved"


class LinkedRecordType(str, enum.Enum):
    transaction = "transaction"
    unit = "unit"
    repair_job = "repair_job"
    sale = "sale"
    lease_account = "lease_account"
    payment = "payment"


class ExceptionType(str, enum.Enum):
    missing_receipt = "missing_receipt"
    missing_assignment = "missing_assignment"
    missing_coding = "missing_coding"
    duplicate_suspected = "duplicate_suspected"
    review_needed = "review_needed"
    invalid_status_transition = "invalid_status_transition"
    inventory_variance = "inventory_variance"
    close_blocker = "close_blocker"
    missing_revenue_stream = "missing_revenue_stream"
    unmatched_customer_payment = "unmatched_customer_payment"
    negative_balance_inconsistency = "negative_balance_inconsistency"
    missing_linked_unit_or_customer = "missing_linked_unit_or_customer"


class ExceptionStatus(str, enum.Enum):
    open = "open"
    in_review = "in_review"
    resolved = "resolved"
    dismissed = "dismissed"


class InvoiceType(str, enum.Enum):
    sale = "sale"
    repair = "repair"
    rto_payment = "rto_payment"


class InvoiceStatus(str, enum.Enum):
    open = "open"
    paid = "paid"
