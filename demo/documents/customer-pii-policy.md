# Customer PII Policy

Customer data must be handled as personally identifiable information. The `customers` dataset contains `email`, `address`, first name, last name, and a stable `customer_id`. Customer Analytics owns the business use of this information and approves analytics access; Data Platform applies technical access controls.

Only use the minimum fields required for a documented purpose. Dashboards should prefer aggregated data and pseudonymous `customer_id` values. Do not copy email or address into ad hoc extracts, support tickets, or unrestricted reporting datasets. When a CLV analysis uses `customer_lifetime_value`, join to `customers` only in an approved environment and omit direct identifiers from the result.

Review requests for new customer-data access quarterly. Report suspected exposure immediately, include the dataset and fields involved, preserve relevant audit data, and follow the privacy incident process. This policy applies even when a document uses a generic label such as customer profile or contact details.
