# CiteBot evaluation datasets

Each directory contains versioned JSON datasets loaded by `citebot-eval`. Gold cases
may include retrieval expectations, atomic claim verdicts, source-anchor IDs, review or
abstention requirements, structured-field expectations, calculation outputs, and diff
operations. Generated cases are exploratory only; CI gates must use human-adjudicated
cases and record parser, model, workflow, and schema hashes.
