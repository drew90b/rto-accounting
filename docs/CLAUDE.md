\# CLAUDE.md



\## Project Overview



This repository contains the MVP for an internal finance operations system for a small used car and golf cart business.



The purpose of the system is to create a simple but disciplined operating backbone for:

\- car and golf cart inventory tracking

\- purchase and repair cost tracking

\- sales, lease, and collections tracking

\- receipt/document linkage

\- exception handling

\- month-end close support

\- accountant-friendly exports



This is not a full ERP or general ledger. It is an operational finance control system designed for a small team with limited accounting discipline and limited technical skill.



---



\## Business Model and Revenue Streams



The business has two primary lines:

\- cars

\- golf carts



The system must support and clearly distinguish all primary revenue streams:



\### Golf cart revenue

1\. golf cart sales

2\. golf cart repairs

3\. golf cart parts sales



\### Car revenue

4\. car sales

5\. car rent-to-own / lease collections

6\. car repair revenue (materials and labor billed to customers)

Note: Not all repair jobs generate revenue. BHPH customer support repairs may be performed at no cost or below cost to support lease customers. These should still be tracked as repair jobs but may not generate repair_revenue transactions.



These revenue streams must be explicit in:

\- transaction entry

\- data model

\- reporting

\- exports

\- dashboards

\- month-end review



Do not collapse these streams into a single generic revenue category.



---



\## Core Product Goals



The system must:



1\. capture financially relevant business events in one place

2\. connect revenue and spend to units, jobs, or overhead

3\. distinguish revenue by business line and revenue stream

4\. prevent silent failures through visible exception queues

5\. link receipts and documents to transactions

6\. support monthly review and close

7\. provide Excel-friendly views and exports for accountant review

8\. remain simple enough for low-skill users



---



\## Primary Users



\- Owner / operator

\- Office administrator

\- Salesperson

\- Site manager

\- Mechanics (limited workflow only)

\- Accountant (primarily through exports and review views)



---



\## Architecture Decision



Use a Python web application as the system of record.



\### Preferred stack

\- FastAPI

\- SQLAlchemy

\- Alembic

\- PostgreSQL

\- Jinja templates or lightweight frontend

\- Pandas / openpyxl for Excel export

\- local file storage for receipts initially

\- role-based authentication



\### Key design principles

\- The database is the source of truth.

\- Excel is a review and export layer, not the core system of record.

\- Receipts and documents are stored outside the database and linked by metadata.

\- The UI should provide spreadsheet-style table views for review and export.



---



\## Functional Priorities



Build in this order:



1\. units register

2\. customer register

3\. purchase entry

4\. repair job entry

5\. sales entry

6\. lease / RTO account entry

7\. collections entry

8\. receipt upload and linkage

9\. exception queue

10\. spreadsheet-style transaction register

11\. month-end export pack

12\. inventory count and variance workflow

13\. dashboards and cash warnings



Do not start with advanced dashboards or overbuilt automation.



---



\## Operating Model



The system should support this practical operating model:



\### Unit lifecycle

A car or golf cart enters inventory, receives purchase and repair cost accumulation, may be sold or leased, and eventually reaches a closed status.



\### Revenue capture model

Revenue should be entered according to the real business event:

\- sale of a golf cart

\- repair of a golf cart

\- sale of golf cart parts

\- sale of a car

\- car lease / RTO charge or collection

\- repair of a car



\### Cost capture model

Costs should be linked to:

\- a specific unit

\- a specific repair job

\- overhead

\- or a mixed case that requires review



\### Accountant review model

The accountant should be able to review records in Excel-style exports and browser-based registers without relying on the live app for day-to-day operational entry.



---



\## Required Modules



\### 1. Units Register

Track:

\- cars

\- golf carts



Fields should include:

\- internal unit ID

\- unit type

\- business line

\- VIN / serial number

\- year

\- make

\- model

\- purchase date

\- purchase source

\- acquisition cost

\- current status

\- repair status

\- sales status

\- lease / RTO status if applicable

\- linked customer if sold or leased

\- linked transactions



Suggested statuses:

\- acquired

\- in inspection

\- in repair

\- waiting parts

\- frontline ready

\- sold

\- leased / RTO active

\- closed

\- returned / special review



---



\### 2. Customer Register

Track customers for:

\- car sales

\- car lease / RTO accounts

\- golf cart sales

\- repair jobs

\- collections



Fields should include:

\- customer ID

\- full name

\- phone

\- email

\- address

\- notes

\- active/inactive status



For later phases, this can be expanded, but keep MVP simple.



---



\### 3. Transactions

Track:

\- inventory purchases

\- parts/materials purchases

\- outsourced labor

\- overhead spend

\- golf cart sales

\- golf cart repair revenue

\- golf cart parts sales revenue

\- car sales

\- car lease / RTO charges

\- down payments

\- customer collections

\- car repair revenue (when repairs are billed to a customer)

\- manual adjustments



Required transaction attributes:

\- transaction ID

\- transaction date

\- entry date

\- transaction type

\- business line

\- revenue stream

\- vendor or customer

\- amount

\- description

\- unit ID if applicable

\- repair job ID if applicable

\- sale ID if applicable

\- lease account ID if applicable

\- category

\- payment method if applicable

\- receipt attached yes/no

\- coding complete yes/no

\- review status

\- exception status

\- entered by



\### Required classification structure

Claude should model these dimensions separately:



\- `business\_line`

&nbsp; - car

&nbsp; - golf\_cart



\- `revenue\_stream`

&nbsp; - golf\_cart\_sale

&nbsp; - golf\_cart\_repair

&nbsp; - golf\_cart\_parts\_sale

&nbsp; - car\_sale

&nbsp; - car\_rto\_lease

&nbsp; - car\_repair



\- `transaction\_type`

&nbsp; - purchase

&nbsp; - sale

&nbsp; - charge

&nbsp; - collection

&nbsp; - repair\_revenue

&nbsp; - parts\_revenue

&nbsp; - labor\_cost

&nbsp; - materials\_cost

&nbsp; - overhead

&nbsp; - adjustment



Do not merge these into one field.



---



\### 4. Repair Jobs

The system must support repair jobs for both cars and golf carts.



Fields should include:

\- repair job ID

\- business line

\- related unit ID if applicable

\- customer ID if customer-owned repair

\- job type

\- open date

\- close date

\- status

\- labor amount

\- materials amount

\- total billed amount

\- notes



Repair jobs should support three operational cases:

\- internal reconditioning tied to inventory units

\- customer-paid repair work that generates repair revenue

\- BHPH customer support repairs performed at no cost or below cost



The system should distinguish:

\- internal cost accumulation (inventory recon)

\- repair revenue from customer-paid repairs

\- BHPH support repairs that may generate little or no revenue



---



\### 5. Sales Records

The system must support outright sale records.



Fields should include:

\- sale ID

\- customer ID

\- unit ID

\- sale date

\- business line

\- sale amount

\- down payment

\- fees if used

\- total contract amount

\- status

\- notes



This should support:

\- car sales

\- golf cart sales



---



\### 6. Lease / RTO Accounts

The system must support car rent-to-own / lease accounts.



Fields should include:

\- lease account ID

\- customer ID

\- unit ID

\- deal date

\- original agreed amount

\- down payment

\- financed or lease balance

\- scheduled payment amount

\- payment frequency

\- status

\- outstanding balance

\- delinquency status

\- notes



This does not need to be a full loan servicing platform in MVP.

It does need to support:

\- deal setup

\- balance tracking

\- payment history

\- status visibility



---



\### 7. Collections / Payments

The system must capture payment events separately from original sales or lease setup.



Fields should include:

\- payment ID

\- customer ID

\- payment date

\- amount

\- payment method

\- related sale ID if applicable

\- related lease account ID if applicable

\- related repair job ID if applicable

\- notes

\- entered by



This allows:

\- down payments

\- regular RTO / lease payments

\- repair payments

\- partial payments



Do not mix payment events into the original sale record without a separate payments table.



---



\### 8. Receipt / Document Linkage

Files should be stored outside the database and linked by metadata.



Required metadata:

\- document ID

\- linked record type

\- linked record ID

\- file path

\- original file name

\- file type

\- upload timestamp

\- uploaded by

\- optional notes



Possible linked record types:

\- transaction

\- unit

\- repair\_job

\- sale

\- lease\_account

\- payment



---



\### 9. Exception Queue

The system must surface incomplete or suspicious records.



Exception types include:

\- missing receipt

\- missing assignment

\- missing coding

\- duplicate suspected

\- review needed

\- invalid status transition

\- inventory variance

\- close blocker

\- missing revenue stream

\- unmatched customer payment

\- negative balance inconsistency

\- missing linked unit or customer



Each exception needs:

\- exception ID

\- type

\- linked record type

\- linked record ID

\- opened date

\- owner

\- status

\- notes

\- target resolution date

\- resolution action

\- audit history



---



\### 10. Spreadsheet-Style Registers

Provide browser-based table views for:

\- transactions

\- units

\- customers

\- sales

\- lease / RTO accounts

\- payments

\- repair jobs

\- exceptions



These should support:

\- filtering

\- sorting

\- search

\- column selection

\- export to Excel

\- limited inline edits where appropriate



These views should make it easy for the accountant or manager to review:

\- revenue by stream

\- spend by category

\- cost by unit

\- payment history

\- unresolved exceptions



---



\### 11. Accountant Export Pack

Support monthly exports for:

\- transaction register

\- categorized spend

\- revenue by stream

\- unresolved exceptions

\- inventory variances

\- sales summary

\- lease / RTO collections summary

\- repair revenue summary

\- unit profitability

\- month-end review checklist



Preferred format:

\- one Excel workbook with multiple tabs

\- each tab clearly named

\- columns stable and consistent across months



---



\## Recommended MVP Data Model



Claude should use a relational data model with the following core entities:



\- `users`

\- `roles`

\- `customers`

\- `units`

\- `vendors`

\- `repair\_jobs`

\- `sales`

\- `lease\_accounts`

\- `payments`

\- `transactions`

\- `documents`

\- `exceptions`

\- `inventory\_counts`

\- `close\_periods`



\### Entity relationship guidance



\- one `customer` can have many `sales`

\- one `customer` can have many `lease\_accounts`

\- one `customer` can have many `payments`

\- one `unit` can have many `transactions`

\- one `unit` can have zero or many `repair\_jobs`

\- one `unit` can have zero or one active `sale`

\- one `unit` can have zero or one active `lease\_account`

\- one `repair\_job` can have many `transactions`

\- one `sale` can have many `payments`

\- one `lease\_account` can have many `payments`

\- one record of many types can have many `documents`

\- one record of many types can have many `exceptions`



\### Important design rule

Do not design the schema around a single overloaded table with ambiguous meanings.

Use separate tables for:

\- units

\- repair jobs

\- sales

\- lease accounts

\- payments

\- documents

\- exceptions



Transactions may act as the detailed financial event log, but they should reference these more specific business entities.



---



\## UX Rules



This system is for low-skill users.

Design accordingly.



\### Required UX principles

\- short forms

\- clear labels

\- limited required fields

\- obvious statuses

\- visible next actions

\- forgiving workflows

\- minimal accounting jargon



\### Avoid

\- dense enterprise screens

\- complicated accounting workflows

\- too many required fields

\- hidden automation that users cannot understand



---



\## Non-Goals



Do not build these in the MVP:

\- full general ledger

\- payroll replacement

\- full CRM

\- full loan servicing platform

\- mobile app

\- advanced forecasting engine

\- enterprise ERP features

\- complex workflow engine



---



\## Development Rules



When generating code or proposing changes:



1\. prefer simple, maintainable patterns

2\. optimize for operational clarity over elegance

3\. make errors and missing data visible

4\. preserve auditability for key records

5\. keep forms and workflows short

6\. avoid unnecessary abstractions

7\. do not overengineer for future scale

8\. include seed data where useful

9\. create real files, not just explanations

10\. keep exports accountant-friendly

11\. make revenue streams explicit in the data model, UI, and reports

12\. separate sales, lease, parts, and repair revenue clearly

13\. separate setup records from payment events

14\. keep Excel exports stable and easy for an accountant to review



---



\## Repository Expectations



Suggested structure:



\- `app/`

\- `app/models/`

\- `app/routes/`

\- `app/services/`

\- `app/templates/`

\- `app/static/`

\- `storage/receipts/`

\- `scripts/`

\- `tests/`

\- `docs/`

\- `sample\_data/`



---



\## Output Expectations for Claude



When asked to scaffold or extend the system:



\- create actual files

\- keep the architecture consistent

\- explain key decisions briefly

\- include setup instructions

\- include sample seed data when useful

\- include migrations when schema changes are introduced

\- include export capability for table-based accountant review

\- preserve explicit revenue-stream tracking throughout the system

\- generate real schema, routes, templates, and sample workflows



---



\## Success Criteria



The MVP is successful if it can:

\- capture purchases and collections in one place

\- link spend to units/jobs/overhead

\- attach or link receipts

\- track revenue by stream

\- distinguish car sales, car lease/RTO income, repairs, parts sales, and golf cart activity

\- support repair jobs cleanly

\- support customer and unit lookup cleanly

\- separate payment history from original deals

\- surface incomplete records

\- provide spreadsheet-style review screens

\- export monthly review packs to Excel

\- improve month-end visibility without overwhelming the team

