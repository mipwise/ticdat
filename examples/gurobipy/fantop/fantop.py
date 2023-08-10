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
# Can read from .csv, Access, Excel, JSON or SQLite files. Self validates the input data
# before solving to prevent strange errors or garbage-in, garbage-out problems.

from ticdat import TicDatFactory, standard_main


try: # if you don't have gurobipy installed, the code will still load and then fail on solve
    import gurobipy as gp
except:
    gp = None

# ------------------------ define the input schema --------------------------------
input_schema = TicDatFactory (
 parameters = [["Parameter"],["Value"]],
 players = [['Player Name'],
            ['Position', 'Average Draft Position', 'Expected Points', 'Draft Status']],
 roster_requirements = [['Position'],
                       ['Min Num Starters', 'Max Num Starters', 'Min Num Reserve', 'Max Num Reserve',
                        'Flex Status']],
 my_draft_positions = [['Draft Position'],[]]
)

# add foreign key constraints (optional, but helps with preventing garbage-in, garbage-out)
input_schema.add_foreign_key("players", "roster_requirements", ['Position', 'Position'])

# set data types (optional, but helps with preventing garbage-in, garbage-out)
input_schema.set_data_type("players", "Average Draft Position", min=0, max=float("inf"),
                          inclusive_min = False, inclusive_max = False)
input_schema.set_data_type("players", "Expected Points", min=-float("inf"), max=float("inf"),
                          inclusive_min = False, inclusive_max = False)
input_schema.set_data_type("players", "Draft Status",
                          strings_allowed = ["Un-drafted", "Drafted By Me", "Drafted By Someone Else"],
                          number_allowed = False)
for fld in ("Min Num Starters",  "Min Num Reserve", "Max Num Reserve"):
    input_schema.set_data_type("roster_requirements", fld, min=0, max=float("inf"),
                          inclusive_min = True, inclusive_max = False, must_be_int = True)
input_schema.set_data_type("roster_requirements", "Max Num Starters", min=0, max=float("inf"),
                      inclusive_min = False, inclusive_max = True, must_be_int = True)
input_schema.set_data_type("roster_requirements", "Flex Status", number_allowed = False,
                          strings_allowed = ["Flex Eligible", "Flex Ineligible"])
input_schema.set_data_type("my_draft_positions", "Draft Position", min=0, max=float("inf"),
                          inclusive_min = False, inclusive_max = False, must_be_int = True)

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
solution_schema = TicDatFactory(
    parameters = [["Parameter"],["Value"]],
    my_draft = [['Player Name'], ['Draft Position', 'Position', 'Planned Or Actual',
                                  'Starter Or Reserve']])
# ---------------------------------------------------------------------------------

# Foresta app building tool needs to tweak the field types for one input field and 4 solution fields
roundoff_configurations = {"input_configurations": {"field_types": {("players", "Position"): "text"}},
                           "solution_configurations": {"field_types": {("parameters", "Value"): "text"}}}
for _ in ["Position", "Planned Or Actual", "Starter Or Reserve"]:
    roundoff_configurations["solution_configurations"]["field_types"]["my_draft", _] = "text"

# ------------------------ create a solve function --------------------------------
def solve(dat):
    assert input_schema.good_tic_dat_object(dat)
    assert not input_schema.find_foreign_key_failures(dat)
    assert not input_schema.find_data_type_failures(dat)
    assert not input_schema.find_data_row_failures(dat)

    expected_draft_position = {}
    # for our purposes, its fine to assume all those drafted by someone else are drafted
    # prior to any players drafted by me
    for player_name in sorted(dat.players,
                              key=lambda _p: {"Un-drafted":dat.players[_p]["Average Draft Position"],
                                              "Drafted By Me":-1,
                                              "Drafted By Someone Else":-2}[dat.players[_p]["Draft Status"]]):
        expected_draft_position[player_name] = len(expected_draft_position) + 1
    assert max(expected_draft_position.values()) == len(set(expected_draft_position.values())) == len(dat.players)
    assert min(expected_draft_position.values()) == 1

    already_drafted_by_me = {player_name for player_name,row in dat.players.items() if
                            row["Draft Status"] == "Drafted By Me"}
    can_be_drafted_by_me = {player_name for player_name,row in dat.players.items() if
                            row["Draft Status"] != "Drafted By Someone Else"}

    m = gp.Model('fantop')
    my_starters = {player_name:m.addVar(vtype=gp.GRB.BINARY, name="starter_%s" % player_name)
                  for player_name in can_be_drafted_by_me}
    my_reserves = {player_name:m.addVar(vtype=gp.GRB.BINARY, name="reserve_%s" % player_name)
                  for player_name in can_be_drafted_by_me}


    for player_name in can_be_drafted_by_me:
        if player_name in already_drafted_by_me:
            m.addConstr(my_starters[player_name] + my_reserves[player_name] == 1,
                        name="already_drafted_%s"%player_name)
        else:
            m.addConstr(my_starters[player_name] + my_reserves[player_name] <= 1,
                        name="cant_draft_twice_%s"%player_name)

    for i,draft_position in enumerate(sorted(dat.my_draft_positions)):
        m.addConstr(gp.quicksum(my_starters[player_name] + my_reserves[player_name]
                                for player_name in can_be_drafted_by_me
                                if expected_draft_position[player_name] < draft_position) <= i,
                    name = "at_most_%s_can_be_ahead_of_%s"%(i,draft_position))

    my_draft_size = gp.quicksum(my_starters[player_name] + my_reserves[player_name]
                                for player_name in can_be_drafted_by_me)
    m.addConstr(my_draft_size >= len(already_drafted_by_me) + 1,
                name = "need_to_extend_by_at_least_one")
    m.addConstr(my_draft_size <= len(dat.my_draft_positions), name = "cant_exceed_draft_total")

    for position, row in dat.roster_requirements.items():
        players = {player_name for player_name in can_be_drafted_by_me
                   if dat.players[player_name]["Position"] == position}
        starters = gp.quicksum(my_starters[player_name] for player_name in players)
        reserves = gp.quicksum(my_reserves[player_name] for player_name in players)
        m.addConstr(starters >= row["Min Num Starters"], name = "min_starters_%s"%position)
        m.addConstr(starters <= row["Max Num Starters"], name = "max_starters_%s"%position)
        m.addConstr(reserves >= row["Min Num Reserve"], name = "min_reserve_%s"%position)
        m.addConstr(reserves <= row["Max Num Reserve"], name = "max_reserve_%s"%position)

    parameters = input_schema.create_full_parameters_dict(dat)
    flex_players = {player_name for player_name in can_be_drafted_by_me if
                    dat.roster_requirements[dat.players[player_name]["Position"]]["Flex Status"] == "Flex Eligible"}
    m.addConstr(gp.quicksum(my_starters[player_name] for player_name in flex_players)
                <= parameters["Maximum Number of Flex Starters"],
                name = "max_flex")

    starter_weight = parameters["Starter Weight"]
    reserve_weight = parameters["Reserve Weight"]
    m.setObjective(gp.quicksum(dat.players[player_name]["Expected Points"] *
                               (my_starters[player_name] * starter_weight + my_reserves[player_name] * reserve_weight)
                               for player_name in can_be_drafted_by_me),
                   sense=gp.GRB.MAXIMIZE)

    m.optimize()

    if m.status != gp.GRB.OPTIMAL:
        print("No draft at all is possible!")
        return

    sln = solution_schema.TicDat()
    def almostone(x):
        return abs(x.x-1) < 0.0001
    picked = sorted([player_name for player_name in can_be_drafted_by_me
                     if almostone(my_starters[player_name]) or almostone(my_reserves[player_name])],
                    key=lambda _p: expected_draft_position[_p])
    assert len(picked) <= len(dat.my_draft_positions)
    if len(picked) < len(dat.my_draft_positions):
        print("Your model is over-constrained, and thus only a partial draft was possible")

    draft_yield = 0
    for player_name, draft_position in zip(picked, sorted(dat.my_draft_positions)):
        draft_yield += dat.players[player_name]["Expected Points"] * \
                       (starter_weight if almostone(my_starters[player_name]) else reserve_weight)
        assert draft_position <= expected_draft_position[player_name]
        sln.my_draft[player_name]["Draft Position"] = draft_position
        sln.my_draft[player_name]["Position"] = dat.players[player_name]["Position"]
        sln.my_draft[player_name]["Planned Or Actual"] = "Actual" if player_name in already_drafted_by_me else "Planned"
        sln.my_draft[player_name]["Starter Or Reserve"] = \
            "Starter" if almostone(my_starters[player_name]) else "Reserve"
    sln.parameters["Total Yield"] = draft_yield
    sln.parameters["Draft Performed"] = "Complete" if len(sln.my_draft) == len(dat.my_draft_positions) \
                                         else "Partial"
    return sln
# ---------------------------------------------------------------------------------

# ------------------------ provide stand-alone functionality ----------------------
# when run from the command line, will read/write json/xls/csv/db/mdb files
if __name__ == "__main__":
    standard_main(input_schema, solution_schema, solve)
# ---------------------------------------------------------------------------------