import random
import string
import typing

import voxelbotutils as utils

from cogs.utils.customised_tree_user import CustomisedTreeUser
from cogs.utils.family_tree.relationship_string_simplifier import RelationshipStringSimplifier as Simplifier
from cogs.utils.discord_name_manager import DiscordNameManager



def get_random_string(length:int=10) -> str:
    return ''.join(random.choices(string.ascii_letters, k=length))


class FamilyTreeMember(object):
    """
    A class representing a member of a family.
    """

    all_users: typing.Dict[typing.Tuple[int, int], 'FamilyTreeMember'] = {}
    INVISIBLE = "[shape=circle, label=\"\", height=0.001, width=0.001]"  # For the DOT script

    __slots__ = ('id', '_children', '_parent', '_partner', 'tree_id', '_guild_id')

    def __init__(self, discord_id:int, children:list=None, parent_id:int=None, partner_id:int=None, guild_id:int=0):
        self.id: int = discord_id
        self._children: typing.List[int] = children or list()
        self._parent: int = parent_id
        self._partner: int = partner_id
        self._guild_id: int = guild_id
        self.tree_id: str = get_random_string()  # Used purely for the dot joining two spouses in the GZ script
        self.all_users[(self.id, self._guild_id)] = self

    def __hash__(self):
        return hash((self.id, self._guild_id,))

    @classmethod
    def get(cls, discord_id:int, guild_id:int=0) -> 'FamilyTreeMember':
        """
        Gives you the object pertaining to the given user ID.

        Args:
            discord_id (int): The ID of the Discord user we want to get the information off.
            guild_id (int, optional): The ID of the guild that we want to get the user from.

        Returns:
            FamilyTreeMember: The family member we've queried for.
        """

        if discord_id is None:
            return None
        v = cls.all_users.get((discord_id, guild_id))
        if v:
            return v
        return cls(discord_id=discord_id, guild_id=guild_id)

    @classmethod
    def get_multiple(cls, *discord_ids:int, guild_id:int=0) -> typing.List['FamilyTreeMember']:
        """
        Gets multiple objects from the cache.
        """

        for i in discord_ids:
            yield cls.get(i, guild_id)

    def to_json(self) -> dict:
        """
        Converts the object to JSON format so you can throw it through Redis.
        """

        return {
            'discord_id': self.id,
            'children': self._children,
            'parent_id': self._parent,
            'partner_id': self._partner,
            'guild_id': self._guild_id,
        }

    @classmethod
    def from_json(cls, data:dict) -> 'FamilyTreeMember':
        """
        Loads an FamilyTreeMember object from JSON.

        Args:
            data (dict): The JSON object that represent the FamilyTreeMember object.

        Returns:
            FamilyTreeMember: The new FamilyTreeMember object.
        """

        return cls(**data)

    def __repr__(self) -> str:
        return f"FamilyTreeMember[{self.id} <Partner {self._partner} <{len(self._children)} children>]"

    def __eq__(self, other) -> bool:
        if not isinstance(other, self.__class__):
            return False
        return all([
            self.id == other.id,
            self._guild_id == other._guild_id,
        ])

    @property
    def partner(self) -> typing.Optional['FamilyTreeMember']:
        """
        Gets you the instance of this user's partner.
        """

        if self._partner:
            return self.get(self._partner, self._guild_id)
        return None

    @property
    def parent(self) -> typing.Optional['FamilyTreeMember']:
        """
        Gets you the instance of this user's parent.
        """

        if self._parent:
            return self.get(self._parent, self._guild_id)
        return None

    @property
    def children(self) -> typing.List['FamilyTreeMember']:
        """
        Gets you the list of children instances for this user.
        """

        for i in self._children:
            # return [self.get(i, self._guild_id) for i in self._children]
            yield self.get(i, self._guild_id)
        # return []

    def get_direct_relations(self) -> typing.List[int]:
        """
        Gets the direct relation IDs for the given user.
        """

        output = []
        output.extend(self._children)
        output.append(self._parent)
        output.append(self._partner)
        return [i for i in output if i is not None]

    @property
    def is_empty(self) -> bool:
        """
        Is this instance useless?
        """

        return all([
            len(self._children) == 0,
            self._parent is None,
            self._partner is None,
        ])

    def get_relation(self, target_user:'FamilyTreeMember') -> typing.Optional[str]:
        """
        Gets your relation to another given FamilyTreeMember object.

        Args:
            target_user (FamilyTreeMember): The user who we want to get the relationship to.

        Returns:
            typing.Optional[str]: The family tree relationship string.
        """

        text = self.get_unshortened_relation(target_user)
        if text is None:
            return None
        return Simplifier().simplify(text)

    @property
    def family_member_count(self) -> int:
        """
        Returns the number of people in the family.
        """

        family_member_count = 0
        for i in self.span(add_parent=True, expand_upwards=True):
            family_member_count += 1
        return family_member_count

    def span(self, people_list:set=None, add_parent:bool=False, expand_upwards:bool=False) -> typing.Iterable['FamilyTreeMember']:
        """
        Gets a list of every user related to this one
        If "add_parent" and "expand_upwards" are True, then it should add every user in a given tree,
        even if they're related through marriage's parents etc

        Args:
            people_list (list, optional): The list of users who are currently in the tree (so as to avoid recursion)
            add_parent (bool, optional): Whether or not to add the parent of this user to the people list
            expand_upwards (bool, optional): Whether or not to expand upwards in the tree

        Returns:
            typing.Iterable['FamilyTreeMember']: A list of users that this person is related to.
        """

        # Don't add yourself again
        if people_list is None:
            people_list = set()
        if self in people_list:
            return people_list
        people_list.add(self)
        yield self

        # Add your parent
        if expand_upwards and add_parent and self._parent:
            yield from self.parent.span(people_list, add_parent=True, expand_upwards=expand_upwards)

        # Add your children
        if self._children:
            for child in self.children:
                yield from child.span(people_list, add_parent=False, expand_upwards=expand_upwards)

        # Add your partner
        if self._partner:
            yield from self.partner.span(people_list, add_parent=True, expand_upwards=expand_upwards)

    def get_root(self) -> 'FamilyTreeMember':
        """
        Expands backwards into the tree up to a root user.
        Only goes up one line of family so it cannot add your spouse's parents etc.
        """

        # Set a default user to look at
        root_user = self
        already_processed = set()

        while True:
            if root_user in already_processed:
                return root_user
            already_processed.add(root_user)
            if root_user._parent:
                root_user = root_user.parent
            elif root_user._partner:
                partner = root_user.partner
                if partner._parent:
                    root_user = partner.parent
            else:
                return root_user

    def get_unshortened_relation(self, target_user:'FamilyTreeMember', working_relation:list=None, added_already:set=None) -> typing.Optional[str]:
        """
        Gets your relation to the other given user.

        Args:
            target_user (FamilyTreeMember): The user who you want to list the relation to.
            working_relation (list, optional): The list of relation steps it's taking to get.
            added_already (list, optional): So we can keep track of who's been looked at before.

        Returns:
            typing.Optional[str]: The family tree relationship string.
        """

        # Set default values
        if working_relation is None:
            working_relation = []
        if added_already is None:
            added_already = set()

        # You're doing a loop - return None
        if self.id in added_already:
            return None

        # We hit the jackpot - return the made up string
        if target_user.id == self.id:
            ret_string = "'s ".join(working_relation)
            return ret_string

        # Add self to list of checked people
        added_already.add(self.id)

        # Check parent
        if self._parent and self._parent not in added_already:
            parent = self.parent
            x = parent.get_unshortened_relation(
                target_user,
                working_relation=working_relation + ['parent'],
                added_already=added_already
            )
            if x:
                return x

        # Check partner
        if self._partner and self._partner not in added_already:
            partner = self.partner
            x = partner.get_unshortened_relation(
                target_user,
                working_relation=working_relation + ['partner'],
                added_already=added_already
            )
            if x:
                return x

        # Check children
        if self._children:
            children = self.children
            for i in [o for o in children if o not in added_already]:
                x = i.get_unshortened_relation(
                    target_user,
                    working_relation=working_relation + ['child'],
                    added_already=added_already
                )
                if x:
                    return x

        return None

    # async def generate_gedcom_script(self, bot:utils.Bot) -> str:
    #     """
    #     Gives you the INDI and FAM gedcom strings for this family tree.
    #     Includes their spouse, if they have one, and any children.
    #     Small bit of redundancy: a family will be added twice if they have a spouse.

    #     Args:
    #         bot (utils.Bot): The bot instance that should be used to get the names of users.

    #     Returns:
    #         str: The generated GEDCOM script.
    #     """

    #     gedcom_text = []
    #     family_id_cache = {}  # id: family count

    #     for i in self.span(add_parent=True, expand_upwards=True):
    #         name = await bot.get_name(i.id)
    #         working_text = [
    #             f'0 @I{i.tree_id}@ INDI',
    #             f'\t1 NAME {name}'
    #         ]

    #         # If you have a parent, get added to their family
    #         if i._parent:
    #             parent = i.parent
    #             if parent.id in family_id_cache:
    #                 working_text.append(f'\t1 FAMC @F{family_id_cache[parent.id]}@')
    #             elif parent._partner and parent._partner in family_id_cache:
    #                 working_text.append(f'\t1 FAMC @F{family_id_cache[parent._partner]}@')
    #             else:
    #                 working_text.append(f'\t1 FAMC @F{parent.tree_id}@')

    #         # If you have children or a partner, generate a family
    #         if i._children or i._partner:
    #             children = i.children
    #             partner = i.partner

    #             # See if you need to make a new family or be added to one already made
    #             try:
    #                 insert_location = gedcom_text.index(f'\t1 HUSB @I{i.tree_id}@')  # This will throw error if this user is not in a tree already
    #                 working_text.append(f'\t1 FAMS @F{family_id_cache[partner.id]}@')
    #                 family_id_cache[i.id] = partner.tree_id
    #                 for c in children:
    #                     gedcom_text.insert(insert_location, f'\t1 CHIL @I{c.tree_id}@')
    #             except ValueError:
    #                 family_id_cache[i.id] = i.tree_id
    #                 working_text.append(f'\t1 FAMS @F{i.tree_id}@')
    #                 working_text.append(f'0 @F{i.tree_id}@ FAM')
    #                 working_text.append(f'\t1 WIFE @I{i.tree_id}@')
    #                 if i.partner:
    #                     working_text.append(f'\t1 HUSB @I{partner.tree_id}@')
    #                 for c in children:
    #                     working_text.append(f'\t1 CHIL @I{c.tree_id}@')

    #         gedcom_text.extend(working_text)
    #     x = '0 HEAD\n\t1 GEDC\n\t\t2 VERS 5.5\n\t\t2 FORM LINEAGE-LINKED\n\t1 CHAR UNICODE\n' + '\n'.join(gedcom_text) + '\n0 TRLR'
    #     return x

    def generational_span(
            self, people_dict:dict=None, depth:int=0, add_parent:bool=False, expand_upwards:bool=False,
            all_people:set=None, recursive_depth:int=0) -> typing.Dict[int, typing.List['FamilyTreeMember']]:
        """
        Gets a list of every user related to this one.
        If "add_parent" and "expand_upwards" are True, then it should add every user in a given tree,
        even if they're related through marriage's parents etc.

        Args:
            people_dict (dict, optional): The dict of users who are currently in the tree (so as to avoid recursion).
            depth (int, optional): The current generation of the tree span.
            add_parent (bool, optional): Whether or not to add the parent of this user to the people list.
            expand_upwards (bool, optional): Whether or not to expand upwards in the tree.
            all_people (set, optional): A set of all people who this recursive function would look at.
            recursive_depth (int, optional): How far into the recursion you have gone - this is so we don't get recursion errors.

        Returns:
            typing.Dict[int, typing.List['FamilyTreeMember']]: A dictionary of each generation of users.
        """

        # Don't add yourself again
        if people_dict is None:
            people_dict = {}
        if all_people is None:
            all_people = set()
        if self.id in all_people:
            return people_dict
        if recursive_depth >= 500:
            return people_dict
        all_people.add(self.id)

        # Add to dict
        x = people_dict.setdefault(depth, list())
        x.append(self)

        # Add your children
        if self._children:
            children = self.children
            for child in children:
                people_dict = child.generational_span(
                    people_dict, depth=depth + 1, add_parent=False, expand_upwards=expand_upwards,
                    all_people=all_people, recursive_depth=recursive_depth + 1
                )

        # Add your partner
        if self._partner:
            partner = self.partner
            people_dict = partner.generational_span(
                people_dict, depth=depth, add_parent=True, expand_upwards=expand_upwards,
                all_people=all_people, recursive_depth=recursive_depth + 1
            )

        # Add your parent
        if expand_upwards and add_parent and self._parent:
            parent = self.parent
            people_dict = parent.generational_span(
                people_dict, depth=depth - 1, add_parent=True, expand_upwards=expand_upwards,
                all_people=all_people, recursive_depth=recursive_depth + 1
            )

        # Remove dupes, should they be in there
        return people_dict

    async def to_dot_script(self, bot:utils.Bot, customised_tree_user:CustomisedTreeUser=None) -> str:
        """
        Gives you a string of the current family tree that will go through DOT.

        Args:
            bot (utils.Bot): The bot instance that should be used to get the names of users.
            customised_tree_user (CustomisedTreeUser, optional): The customised tree object that should be used to alter how the
                dot script looks.

        Returns:
            str: The generated DOT code.
        """

        root_user = self.get_root()
        gen_span = root_user.generational_span()
        return await self.to_dot_script_from_generational_span(bot, gen_span, customised_tree_user)

    async def to_full_dot_script(self, bot:utils.Bot, customised_tree_user:CustomisedTreeUser=None) -> str:
        """
        Gives you the string of the FULL current family.

        Args:
            bot (utils.Bot): The bot instance that should be used to get the names of users.
            customised_tree_user (CustomisedTreeUser, optional): The customised tree object that should be used to alter how the
                dot script looks.

        Returns:
            str: The generated DOT code.
        """

        root_user = self.get_root()
        gen_span = root_user.generational_span(expand_upwards=True, add_parent=True)
        return await self.to_dot_script_from_generational_span(bot, gen_span, customised_tree_user)

    async def to_dot_script_from_generational_span(self, bot:utils.Bot, gen_span:dict, customised_tree_user:CustomisedTreeUser) -> str:
        """
        Generates the DOT script from a given generational span.

        Args:
            bot (utils.Bot): The bot instance that should be used to get the names of users.
            gen_span (dict): The generational span.
            customised_tree_user (CustomisedTreeUser, optional): The customised tree object that should be used to alter how the
                dot script looks.

        Returns:
            str: The generated DOT code.
        """

        # Find my own depth
        my_depth: int = None or 0
        for depth, depth_list in gen_span.items():
            if self in depth_list:
                my_depth = depth
                break

        # Add my partner and parent
        if self._partner:
            partner = self.partner
            if partner not in gen_span.get(my_depth, list()):
                x = gen_span.get(my_depth, list())
                x.append(partner)
                gen_span[my_depth] = x
        if self._parent:
            parent = self.parent
            if parent not in gen_span.get(my_depth - 1, list()):
                x = gen_span.get(my_depth - 1, list())
                x.append(parent)
                gen_span[my_depth - 1] = x

        # Make some initial digraph stuff
        all_text: str = (
            "digraph {"
            f"node [shape=box,fontcolor={customised_tree_user.hex['font']},color={customised_tree_user.hex['edge']},"
            f"fillcolor={customised_tree_user.hex['node']},style=filled];"
            f"edge [dir=none,color={customised_tree_user.hex['edge']}];"
            f"bgcolor={customised_tree_user.hex['background']};"
            f"rankdir={customised_tree_user.hex['direction']};"
        )

        # Set up some stuff for later
        all_users: typing.Set['FamilyTreeMember'] = set()
        user_parent_tree: typing.Dict['FamilyTreeMember', str] = {}  # Connects a parent to a random string used to connect the children

        # Add the username for each user (from unflattened list)
        for generation in gen_span.values():
            for i in generation:
                name = await DiscordNameManager.fetch_name_by_id(bot, i.id)
                if name is None:
                    continue
                all_users.add(i)
                name = name.replace('"', '\\"')
                if i == self:
                    all_text += f'{i.id}[label="{name}", fillcolor={customised_tree_user.hex["highlighted_node"]}, fontcolor={customised_tree_user.hex["highlighted_font"]}];'
                else:
                    all_text += f'{i.id}[label="{name}"];'

        # Order the generations
        generation_numbers: typing.List[int] = sorted(list(gen_span.keys()))  # The ordered list of generation numbers - just a list of sequential numbers

        # Go through the members for each generation
        for generation_number in generation_numbers:
            generation = gen_span.get(generation_number)

            # Make sure you don't add a spouse twice
            added_already: typing.List[self.__class__] = []

            # Add a ranking for this generation
            all_text += "{rank=same;"

            # Add linking
            previous_person = None

            # Go through each person in the generation
            for person in generation:

                # Don't add a person twice
                if person in added_already:
                    continue
                added_already.append(person)
                partner = person.partner

                # Give them something in the dict so it doesn't make a keyerror
                user_parent_tree[person.id] = person.id

                # Make sure they stay in line
                if previous_person:
                    all_text += f"{previous_person.id} -> {person.id} [style=invis];"

                # Add the user and their partner
                if partner and partner in generation:

                    # Set their user parent tree so they share a family value
                    user_parent_tree[partner.id] = user_parent_tree[person.id] = get_random_string()

                    # Add the users and family value
                    all_text += f"{person.id} -> {user_parent_tree[person.id]} -> {partner.id};"
                    all_text += f"{user_parent_tree[person.id]} {self.INVISIBLE};"
                    added_already.append(partner)
                    previous_person = partner

                # No partner? No problem
                else:
                    all_text += f"{person.id};"
                    previous_person = person

            # Close off the generation and open a new ranking for adding children
            all_text += "}{"

            # Go through the people in the generation and add add links
            for person in generation:
                if person._children:
                    if any([i in all_users for i in person.children]):
                        all_text += f"h{user_parent_tree[person.id]} {self.INVISIBLE};"
            all_text += "}"

            # Add the lines from parent to node to child
            added_already.clear()
            for person in generation:
                if person._children:
                    if any([i in all_users for i in person.children]):
                        if user_parent_tree[person.id] in added_already:
                            pass
                        else:
                            all_text += f"\t\t{user_parent_tree[person.id]} -> h{user_parent_tree[person.id]};"
                            added_already.append(user_parent_tree[person.id])
                        for child in [i for i in person.children if i in all_users]:
                            all_text += f"\t\th{user_parent_tree[person.id]} -> {child.id};"

        # And we're done!
        all_text += "}"
        return all_text
