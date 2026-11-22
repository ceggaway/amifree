# Report evidence

Screenshots of the read-only queries behind the Section 8 figures and tables, run in the Supabase SQL
Editor against the production project (`cs3216_final_AmIFree`) on 22 November 2026. Each query stops
at the end of 20 November 2026, the report's cut-off, so rerunning it gives the same numbers.

The screenshots show totals only; no individual user's data leaves the database.

| Report | Query | Screenshot | Result |
| --- | --- | --- | --- |
| Figure 9: from signup to calendar | [`scripts/report_figure9_funnel.sql`](../../scripts/report_figure9_funnel.sql) | [figure9_funnel.png](figure9_funnel.png) | 139 started, 67 active, 56 took a useful action, 21 connected a calendar |
| Figure 10: weekly signups | [`scripts/report_figure10_signups.sql`](../../scripts/report_figure10_signups.sql) | [figure10_weekly_signups.png](figure10_weekly_signups.png) | 1, 3, 24, 50, 35, 17, 9 |
| Table 5: calendar connection by signup week | [`scripts/report_table5_calendar_by_week.sql`](../../scripts/report_table5_calendar_by_week.sql) | [table5_calendar_by_week.png](table5_calendar_by_week.png) | 21 of 139 connected |
| Table 6: feature use | [`scripts/report_table6_feature_use.sql`](../../scripts/report_table6_feature_use.sql) | [table6_feature_use.png](table6_feature_use.png) | 8 features, users and uses |

The definitions behind these counts (active user, useful action, calendar connected) are in Section 8.1
of the report.
