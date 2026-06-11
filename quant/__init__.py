"""Quantitative machinery for the strike-survival model (Phase 1).

Modules:
    conventions  - sigma anchor, time-to-settlement, year-fraction convention
    distance     - normalized distance, grid placement, snap-to-strike (task 1.1)
    pmkt         - analytic no-touch baseline probability (task 1.2)

Internal-consistency rule (CLAUDE.md): everything here shares ONE sigma
source and ONE sqrt(T) convention. Labels (Phase 2) import nothing from this
package - labels are pure price facts.
"""
