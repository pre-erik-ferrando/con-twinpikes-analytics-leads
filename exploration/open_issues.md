# Open issues: who answers what

The questions for TWP and Fluidra come from the table "Pendientes y preguntas para TWP y Fluidra" in
[the definition v3.2](../docs/spec/Lead_Generator_DACH_definicion_v3.2.docx), except the subregion → salesperson
table, which is in the body of the definition. The questions for us come from testing the project so far.

## TWP

1. Rules for the four business types beyond pool specialists.
2. Inputs for Belgium and the Netherlands.
3. Two numbers in the scoring: Austria's figure (21.1 %, before 19.9 %) and whether Belgium and the
   Netherlands really share the same threshold.
4. Whether the cockpit reads the Salesforce file unchanged, when it is switched off, and what replaces it.
5. Who approves a run when it costs more than the limit.
6. Whether Germany has 400 or 401 subregions.
7. A dated copy of their inputs Excel as an annex, and an impact estimate before each change.
8. Whether the [PROPUESTA] marks shown in green are TWP's decision or our starting values.
9. With Fluidra: who provides the address coordinates in the customer lists, or whether they stay optional.
10. Which codes they use for Vienna: 900 for the whole city, or 901-923 for its districts (found in testing).

## Fluidra (TWP passes the questions on)

1. Which salesperson gets the leads of each subregion, and who gets them by default.
2. Customer lists for Belgium and the Netherlands.
3. The Lead Status value to export and the rejection reason in Salesforce.
4. What to do when a lead we sent turns out to be an existing customer.

## Us

1. ~~Fix the Google Maps search~~: done, the area is sent as its outline ([findings.md](findings.md)).
   Still to test: a whole state sent at once.
2. Agree with Roger where the tables go in the database.
3. Which AI model to use, its key, and a spending cap.
4. Find out with Intelek whether their app can launch runs and accept file uploads.
