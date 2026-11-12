# TMDL emitted subset

Reference: https://learn.microsoft.com/en-us/analysis-services/tmdl/tmdl-overview

The generated model uses `database`, `model`, `ref table`, `table`, `column`, `measure`, and an import `partition` containing M. Object names are single-quoted with doubled apostrophes. Text property values are double-quoted with doubled quote characters. Hierarchy indentation uses tabs. Expressions appear after `=`; scalar properties appear after `:`. Separate table files live under `definition/tables/`. An empty `relationships.tmdl` is intentional for the single-table milestone.

This reference is not an executable TMDL parser. Actual syntax acceptance and Desktop refresh must be checked in Power BI Desktop at the explicit step 3 gate. No time-intelligence setting is added to the model.
