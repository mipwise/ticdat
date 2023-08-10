#
# Author: Pete Cacioppi https://lnkd.in/bUhfyNn
#
# Solves a fantasy football drafting problem. Tries to maximize the weighted
# expected points of a draft, while obeying min/max restrictions for different
# positions (to include a maximum-flex-players constraint).
#
# Pre-computes the expected draft position of each player, so as to prevent
# creating a draft plan based on unrealistic expectations of player availability
# at each round.
#
# The current draft standing can be filled in as you go in the drafted table.
# A user can thus re-optimize for each of his draft picks.
#
# Uses the ticdat package to simplify file IO and provide command line functionality.
# Can read from .csv, JSON, Excel or SQLite files. Self validates the input data
# before solving to prevent strange errors or garbage-in, garbage-out problems.
#
# Run like
#  python fantop.py -i fantop_sample_data -o fantop_solution_data

from ticdat import PanDatFactory, standard_main
try: # if you don't have amplpy installed, the code will still load and then fail on solve
    from amplpy import AMPL
except:
    AMPL = None
# ------------------------ define the input schema --------------------------------
input_schema = PanDatFactory(
    parameters=[["Parameter"], ["Value"]],
    players=[['Player Name'],
            ['Position', 'Average Draft Position', 'Expected Points', 'Draft Status']],
    roster_requirements=[['Position'],
                       ['Min Num Starters', 'Max Num Starters', 'Min Num Reserve', 'Max Num Reserve',
                        'Flex Status']],
    my_draft_positions=[['Draft Position'], []]
)

# add foreign key constraints (optional, but helps with preventing garbage-in, garbage-out)
input_schema.add_foreign_key("players", "roster_requirements", ['Position', 'Position'])

# set data types (optional, but helps with preventing garbage-in, garbage-out)
input_schema.set_data_type("players", "Average Draft Position", min=0, max=float("inf"),
                          inclusive_min=False, inclusive_max=False)
input_schema.set_data_type("players", "Expected Points", min=-float("inf"), max=float("inf"),
                          inclusive_min=False, inclusive_max=False)
input_schema.set_data_type("players", "Draft Status",
                          strings_allowed= ["Un-drafted", "Drafted By Me", "Drafted By Someone Else"],
                          number_allowed= False)
for fld in ("Min Num Starters",  "Min Num Reserve"):
    input_schema.set_data_type("roster_requirements", fld, min=0, max=float("inf"),
                          inclusive_min=True, inclusive_max=False, must_be_int=True)
for fld in ("Max Num Starters",  "Min Num Reserve", "Max Num Reserve"):
    input_schema.set_data_type("roster_requirements", fld, min=0, max=float("inf"),
                      inclusive_min=True, inclusive_max=True, must_be_int=True)
input_schema.set_data_type("roster_requirements", "Flex Status", number_allowed = False,
                          strings_allowed=["Flex Eligible", "Flex Ineligible"])
input_schema.set_data_type("my_draft_positions", "Draft Position", min=0, max=float("inf"),
                          inclusive_min=False, inclusive_max=False, must_be_int=True)

input_schema.add_data_row_predicate("roster_requirements",
    predicate=lambda row : row["Max Num Starters"] >= row["Min Num Starters"])
input_schema.add_data_row_predicate("roster_requirements",
    predicate=lambda row : row["Max Num Reserve"] >= row["Min Num Reserve"])

input_schema.add_parameter("Starter Weight", default_value=1.2, min=0, max=float("inf"),
                           inclusive_min=False, inclusive_max=False)
input_schema.add_parameter("Reserve Weight", default_value=0.9, min=0, max=float("inf"),
                           inclusive_min=False, inclusive_max=False)
input_schema.add_parameter("Maximum Number of Flex Starters", default_value=float("inf"), min=0, max=float("inf"),
                           inclusive_min=True, inclusive_max=True)
# ---------------------------------------------------------------------------------

# ------------------------ define the output schema -------------------------------
solution_schema = PanDatFactory(
    parameters=[["Parameter"], ["Value"]],
    my_draft=[['Player Name'], ['Draft Position', 'Position', 'Planned Or Actual',
                                'Starter Or Reserve']])
# ---------------------------------------------------------------------------------

# Foresta app building tool needs to tweak the field types for one input field and 4 solution fields
roundoff_configurations = {"input_configurations": {"field_types": {("players", "Position"): "text"}},
                           "solution_configurations": {"field_types": {("parameters", "Value"): "text"}}}
for _ in ["Position", "Planned Or Actual", "Starter Or Reserve"]:
    roundoff_configurations["solution_configurations"]["field_types"]["my_draft", _] = "text"


# ------------------------ create a solve function --------------------------------
def solve(dat):
    assert input_schema.good_pan_dat_object(dat)
    assert not input_schema.find_duplicates(dat)
    assert not input_schema.find_foreign_key_failures(dat)
    assert not input_schema.find_data_type_failures(dat)
    assert not input_schema.find_data_row_failures(dat)

    parameters = input_schema.create_full_parameters_dict(dat)

    # for our purposes, its fine to assume all those drafted by someone else are drafted
    # prior to any players drafted by me
    dat.players["_temp_sort_column"] = dat.players["Average Draft Position"]
    dat.players.loc[dat.players["Draft Status"] == "Drafted By Someone Else", "_temp_sort_column"] = -2
    dat.players.loc[dat.players["Draft Status"] == "Drafted By Me", "_temp_sort_column"] = -1
    dat.players.sort_values(by="_temp_sort_column", inplace=True)
    dat.players.reset_index(drop=True, inplace=True) # get rid of the index that has become scrambled
    dat.players.reset_index(drop=False, inplace=True) # turn the sequential index into a column
    dat.players["Expected Draft Position"] = dat.players["index"] + 1
    dat.players.drop(["index", "_temp_sort_column"], inplace=True, axis=1)

    # BE CAREFUL - this is https://github.com/ticdat/ticdat/issues/54 Not sure why this is needed
    dat.my_draft_positions.sort_values("Draft Position", inplace=True)

    assert list(dat.players["Expected Draft Position"]) == list(range(1, len(dat.players)+1))

    ampl = AMPL()
    ampl.setOption('solver', 'gurobi')
    ampl.eval("""
    param max_number_of_flex_starters>=0;
    param starter_weight >=0;
    param reserve_weight >= 0;

    set MY_DRAFT_POSITIONS ordered;

    set POSITIONS;
    param min_number_starters{POSITIONS} >= 0, < Infinity;
    param max_number_starters{p in POSITIONS} >= min_number_starters[p];
    param min_number_reserve{POSITIONS} >= 0, < Infinity;
    param max_number_reserve{p in POSITIONS} >= min_number_reserve[p];
    param flex_status{POSITIONS} symbolic within {'Flex Eligible', 'Flex Ineligible'};

    set PLAYERS;
    param draft_status{PLAYERS} symbolic within {'Un-drafted', 'Drafted By Me',  'Drafted By Someone Else'} ;
    param position{PLAYERS} symbolic within {POSITIONS};
    param expected_draft_position{PLAYERS} >=1, < Infinity;
    param expected_points{PLAYERS} > -Infinity, < Infinity;
    set DRAFTABLE_PLAYERS within PLAYERS = {p in PLAYERS : draft_status[p] <> 'Drafted By Someone Else'};

    var Starters {DRAFTABLE_PLAYERS} binary;
    var Reserves {DRAFTABLE_PLAYERS} binary;

    subject to Already_Drafted_By_Me {p in PLAYERS: draft_status[p] = 'Drafted By Me'}:
        Starters[p] + Reserves[p] = 1;
    subject to Cant_Draft_Twice {p in PLAYERS: draft_status[p] = 'Un-drafted'}:
        Starters[p] + Reserves[p] <= 1;

    subject to At_Most_X_Can_Be_Ahead_Of_Y {d in MY_DRAFT_POSITIONS}:
        sum{p in DRAFTABLE_PLAYERS: expected_draft_position[p] < d}(Starters[p] + Reserves[p]) <=
        ord(d, MY_DRAFT_POSITIONS) - 1;

    var My_Draft_Size >= card({p in PLAYERS: draft_status[p] = 'Drafted By Me'}),
                      <= card(MY_DRAFT_POSITIONS);
    subject to Set_My_Draft_Size:
        sum{p in PLAYERS: draft_status[p] <> 'Drafted By Someone Else'}(Starters[p] + Reserves[p]) =
            My_Draft_Size;

    subject to Min_Number_Starters{p in POSITIONS}:
        sum{pl in DRAFTABLE_PLAYERS: position[pl] = p}Starters[pl] >= min_number_starters[p];
    subject to Max_Number_Starters{p in POSITIONS}:
        sum{pl in DRAFTABLE_PLAYERS: position[pl] = p}Starters[pl] <= max_number_starters[p];
    subject to Min_Number_Reserve{p in POSITIONS}:
        sum{pl in DRAFTABLE_PLAYERS: position[pl] = p}Reserves[pl]>= min_number_reserve[p];
    subject to Max_Number_Reserve{p in POSITIONS}:
        sum{pl in DRAFTABLE_PLAYERS: position[pl] = p}Reserves[pl] <= max_number_reserve[p];

    subject to Max_Number_Flex_Starters:
        sum{p in DRAFTABLE_PLAYERS: flex_status[position[p]] = 'Flex Eligible'}Starters[p]
        <= max_number_of_flex_starters;

    maximize Total_Yield:
        sum{p in DRAFTABLE_PLAYERS}(expected_points[p] *
                                  (starter_weight * Starters[p] + reserve_weight * Reserves[p]));
    """)
    # copy the tables to amplpy.DataFrame objects, renaming the data fields as needed
    ampl_dat = input_schema.copy_to_ampl(dat,
        excluded_tables={"parameters"}, # this table isn't passed directly to AMPL
        field_renamings={("players", "Expected Draft Position"): "expected_draft_position",
                         ("players", "Position"): "position",
                         ("players", 'Average Draft Position'): "", # this column isn't passed to AMPL
                         ("players", 'Expected Points'): "expected_points",
                         ("players", 'Draft Status'): "draft_status",
                         ("roster_requirements", 'Min Num Starters'): 'min_number_starters',
                         ("roster_requirements", 'Max Num Starters'): 'max_number_starters',
                         ("roster_requirements", 'Min Num Reserve'): 'min_number_reserve',
                         ("roster_requirements", 'Max Num Reserve'): 'max_number_reserve',
                         ("roster_requirements", 'Flex Status'): 'flex_status',
                         })
    input_schema.set_ampl_data(ampl_dat, ampl, {"players":"PLAYERS", "my_draft_positions":"MY_DRAFT_POSITIONS",
                                                "roster_requirements": "POSITIONS"})
    ampl.param['max_number_of_flex_starters'] = min(parameters['Maximum Number of Flex Starters'],
                                                    len(dat.my_draft_positions))
    ampl.param['starter_weight'] = parameters['Starter Weight']
    ampl.param['reserve_weight'] = parameters['Reserve Weight']

    # solve and recover solutions next
    ampl.solve()
    if ampl.getValue("solve_result") == "infeasible":
        print("No draft at all is possible!")
        return

    def selected_players(df, starter_or_reserve):
        assert len(df.columns) == 1 # df.columns[0] is the name of the column that holds the solution variable result
        # only capture those rows where the solution variable is nearly 1
        df = df[(df[df.columns[0]] - 1).abs() < 0.00001]
        df = df.join(dat.players.set_index('Player Name'))
        df.reset_index(inplace=True)
        df.rename(columns={df.columns[0]: "Player Name"}, inplace=True)
        df["Planned Or Actual"] = "Actual"
        df.loc[df["Draft Status"] == "Un-drafted", "Planned Or Actual"] = "Planned"
        df["Starter Or Reserve"] = starter_or_reserve
        return df[["Player Name", "Position", "Planned Or Actual", "Starter Or Reserve", "Expected Draft Position"]]

    starters = selected_players(ampl.getVariable("Starters").getValues().toPandas(), "Starter")
    reserves = selected_players(ampl.getVariable("Reserves").getValues().toPandas(), "Reserve")
    my_draft = starters.append(reserves)
    my_draft = my_draft.sort_values(by="Expected Draft Position").drop("Expected Draft Position", axis=1)
    my_draft.reset_index(drop=True, inplace=True) # now its index is sorted by Expected Draft Position

    sorted_draft_positions = dat.my_draft_positions.sort_values(by='Draft Position').reset_index(drop=True)
    if len(my_draft) < len(sorted_draft_positions):
        print("Your model is over-constrained, and thus only a partial draft was possible")
    # my_draft and sorted_draft_positions both have sequential index values. join will use these by default
    sln = solution_schema.PanDat(my_draft=my_draft.join(sorted_draft_positions))

    sln.parameters.loc[0] = ["Total Yield", ampl.getObjective('Total_Yield').value()]
    sln.parameters.loc[1] = ["Draft Performed", "Complete" if len(sln.my_draft) == len(dat.my_draft_positions)
                             else "Partial"]

    return sln
# ---------------------------------------------------------------------------------

# ------------------------ provide stand-alone functionality ----------------------
# when run from the command line, will read/write json/xls/csv/db/mdb files
if __name__ == "__main__":
    standard_main(input_schema, solution_schema, solve)
# ---------------------------------------------------------------------------------