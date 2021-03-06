import asyncio
from datetime import datetime as dt

import asyncpg
import discord
from discord.ext import commands
import voxelbotutils as vbu

from cogs import utils


class Parentage(vbu.Cog):

    async def get_max_children_for_member(self, guild: discord.Guild, user: discord.Member) -> int:
        """
        Get the maximum amount of children a given member can have.
        """

        # Bots can do what they want
        if user.bot:
            return 5

        # See how many children they're allowed with Gold
        gold_children_amount = 0
        if self.bot.config.get('is_server_specific', False):
            guild_max_children = self.bot.guild_settings[guild.id].get('max_children')
            if guild_max_children:
                gold_children_amount = max([
                    amount if int(role_id) in user._roles else 0 for role_id, amount in guild_max_children.items()
                ])

        # See how many children they're allowed normally (in regard to Patreon tier)
        marriagebot_perks = await utils.get_marriagebot_perks(self.bot, user.id)
        user_children_amount = marriagebot_perks.max_children

        # Return the largest amount of children they've been assigned that's UNDER the global max children as set in the config
        return min([
            max([
                gold_children_amount,
                user_children_amount,
                utils.TIER_NONE.max_children,
            ]),
            utils.TIER_THREE.max_children,
        ])

    @vbu.command(context_command_type=vbu.ApplicationCommandType.USER, context_command_name="Make user your parent")
    @vbu.cooldown.no_raise_cooldown(1, 3, commands.BucketType.user)
    @vbu.checks.bot_is_ready()
    @commands.guild_only()
    @vbu.bot_has_permissions(send_messages=True, add_reactions=True)
    async def makeparent(self, ctx: vbu.Context, *, target: utils.converters.UnblockedMember):
        """
        Picks a user that you want to be your parent.
        """

        # Variables we're gonna need for later
        family_guild_id = utils.get_family_guild_id(ctx)
        author_tree, target_tree = utils.FamilyTreeMember.get_multiple(ctx.author.id, target.id, guild_id=family_guild_id)

        # Check they're not themselves
        if target.id == ctx.author.id:
            return await ctx.send("That's you. You can't make yourself your parent.", wait=False)

        # Check they're not a bot
        if target.id == self.bot.user.id:
            return await ctx.send("I think I could do better actually, but thank you!", wait=False)

        # Lock those users
        re = await self.bot.redis.get_connection()
        try:
            lock = await utils.ProposalLock.lock(re, ctx.author.id, target.id)
        except utils.ProposalInProgress:
            return await ctx.send("Aren't you popular! One of you is already waiting on a proposal - please try again later.", wait=False)

        # See if the *target* is already married
        if author_tree.parent:
            await lock.unlock()
            return await ctx.send(
                f"Hey! {ctx.author.mention}, you already have a parent \N{ANGRY FACE}",
                allowed_mentions=utils.only_mention(ctx.author),
                wait=False,
            )

        # See if we're already married
        if ctx.author.id in target_tree._children:
            await lock.unlock()
            return await ctx.send(
                f"Hey isn't {target.mention} already your child? \N{FACE WITH ROLLING EYES}",
                allowed_mentions=utils.only_mention(ctx.author),
                wait=False,
            )

        # See if they're already related
        async with ctx.typing():
            relation = author_tree.get_relation(target_tree)
        if relation and utils.guild_allows_incest(ctx) is False:
            await lock.unlock()
            return await ctx.send(
                f"Woah woah woah, it looks like you guys are already related! {target.mention} is your {relation}!",
                allowed_mentions=utils.only_mention(ctx.author),
                wait=False,
            )

        # Manage children
        children_amount = await self.get_max_children_for_member(ctx.guild, target)
        if len(target_tree._children) >= children_amount:
            return await ctx.send(
                f"They're currently at the maximum amount of children they can have - see `{ctx.prefix}perks` for more information.",
                wait=False,
            )

        # Check the size of their trees
        # TODO I can make this a util because I'm going to use it a couple times
        max_family_members = utils.get_max_family_members(ctx)
        async with ctx.typing():
            family_member_count = 0
            for i in author_tree.span(add_parent=True, expand_upwards=True):
                if family_member_count >= max_family_members:
                    break
                family_member_count += 1
            for i in target_tree.span(add_parent=True, expand_upwards=True):
                if family_member_count >= max_family_members:
                    break
                family_member_count += 1
            if family_member_count >= max_family_members:
                await lock.unlock()
                return await ctx.send(
                    f"If you added {target.mention} to your family, you'd have over {max_family_members} in your family. Sorry!",
                    allowed_mentions=utils.only_mention(ctx.author),
                    wait=False,
                )

        # Set up the proposal
        try:
            result = await utils.send_proposal_message(
                ctx, target,
                f"Hey, {target.mention}, {ctx.author.mention} wants to be your child! What do you think?",
                allow_bots=True,
            )
        except Exception:
            result = None
        if result is None:
            return await lock.unlock()

        # Database it up
        async with self.bot.database() as db:
            try:
                await db(
                    """INSERT INTO parents (parent_id, child_id, guild_id, timestamp) VALUES ($1, $2, $3, $4)""",
                    target.id, ctx.author.id, family_guild_id, dt.utcnow(),
                )
            except asyncpg.UniqueViolationError:
                await lock.unlock()
                return await result.ctx.send("I ran into an error saving your family data - please try again later.")
        await result.ctx.send(
            f"I'm happy to introduce {ctx.author.mention} as your child, {target.mention}!",
            wait=False,
        )

        # And we're done
        target_tree._children.append(author_tree.id)
        author_tree._parent = target.id
        await re.publish('TreeMemberUpdate', author_tree.to_json())
        await re.publish('TreeMemberUpdate', target_tree.to_json())
        await re.disconnect()
        await lock.unlock()

    @vbu.command(context_command_type=vbu.ApplicationCommandType.USER, context_command_name="Adopt user")
    @vbu.cooldown.no_raise_cooldown(1, 3, commands.BucketType.user)
    @vbu.checks.bot_is_ready()
    @commands.guild_only()
    @vbu.bot_has_permissions(send_messages=True, add_reactions=True)
    async def adopt(self, ctx: vbu.Context, *, target: utils.converters.UnblockedMember):
        """
        Adopt another user into your family.
        """

        # Variables we're gonna need for later
        family_guild_id = utils.get_family_guild_id(ctx)
        author_tree, target_tree = utils.FamilyTreeMember.get_multiple(ctx.author.id, target.id, guild_id=family_guild_id)

        # Check they're not themselves
        if target.id == ctx.author.id:
            return await ctx.send("That's you. You can't adopt yourself.", wait=False)

        # Check they're not a bot
        if target.bot:
            if target.id == self.bot.user.id:
                return await ctx.send("I think I could do better actually, but thank you!", wait=False)
            return await ctx.send("That is a robot. Robots cannot consent to adoption.", wait=False)

        # Lock those users
        re = await self.bot.redis.get_connection()
        try:
            lock = await utils.ProposalLock.lock(re, ctx.author.id, target.id)
        except utils.ProposalInProgress:
            return await ctx.send("Aren't you popular! One of you is already waiting on a proposal - please try again later.", wait=False)

        # See if the *target* is already married
        if target_tree.parent:
            await lock.unlock()
            return await ctx.send(
                f"Sorry, {ctx.author.mention}, it looks like {target.mention} already has a parent \N{PENSIVE FACE}",
                allowed_mentions=utils.only_mention(ctx.author),
                wait=False,
            )

        # See if we're already married
        if target.id in author_tree._children:
            await lock.unlock()
            return await ctx.send(
                f"Hey, {ctx.author.mention}, they're already your child \N{FACE WITH ROLLING EYES}",
                allowed_mentions=utils.only_mention(ctx.author),
                wait=False,
            )

        # See if they're already related
        async with ctx.typing():
            relation = author_tree.get_relation(target_tree)
        if relation and utils.guild_allows_incest(ctx) is False:
            await lock.unlock()
            return await ctx.send(
                f"Woah woah woah, it looks like you guys are already related! {target.mention} is your {relation}!",
                allowed_mentions=utils.only_mention(ctx.author),
                wait=False,
            )

        # Manage children
        children_amount = await self.get_max_children_for_member(ctx.guild, ctx.author)
        if len(author_tree._children) >= children_amount:
            return await ctx.send(
                f"You're currently at the maximum amount of children you can have - see `{ctx.prefix}perks` for more information.",
                wait=False,
            )

        # Check the size of their trees
        # TODO I can make this a util because I'm going to use it a couple times
        max_family_members = utils.get_max_family_members(ctx)
        async with ctx.typing():
            family_member_count = 0
            for i in author_tree.span(add_parent=True, expand_upwards=True):
                if family_member_count >= max_family_members:
                    break
                family_member_count += 1
            for i in target_tree.span(add_parent=True, expand_upwards=True):
                if family_member_count >= max_family_members:
                    break
                family_member_count += 1
            if family_member_count >= max_family_members:
                await lock.unlock()
                return await ctx.send(
                    f"If you added {target.mention} to your family, you'd have over {max_family_members} in your family. Sorry!",
                    allowed_mentions=utils.only_mention(ctx.author),
                    wait=False,
                )

        # Set up the proposal
        try:
            result = await utils.send_proposal_message(
                ctx, target,
                f"Hey, {target.mention}, {ctx.author.mention} wants to adopt you! What do you think?",
            )
        except Exception:
            result = None
        if result is None:
            return await lock.unlock()

        # Database it up
        async with self.bot.database() as db:
            try:
                await db(
                    """INSERT INTO parents (parent_id, child_id, guild_id, timestamp) VALUES ($1, $2, $3, $4)""",
                    ctx.author.id, target.id, family_guild_id, dt.utcnow(),
                )
            except asyncpg.UniqueViolationError:
                await lock.unlock()
                return await result.ctx.send("I ran into an error saving your family data - please try again later.", wait=False)
        await result.ctx.send(f"I'm happy to introduce {ctx.author.mention} as your parent, {target.mention}!", wait=False)

        # And we're done
        author_tree._children.append(target.id)
        target_tree._parent = author_tree.id
        await re.publish('TreeMemberUpdate', author_tree.to_json())
        await re.publish('TreeMemberUpdate', target_tree.to_json())
        await re.disconnect()
        await lock.unlock()

    @vbu.command(aliases=['abort'])
    @vbu.cooldown.no_raise_cooldown(1, 3, commands.BucketType.user)
    @vbu.checks.bot_is_ready()
    @commands.guild_only()
    @vbu.bot_has_permissions(send_messages=True, add_reactions=True)
    async def disown(self, ctx: vbu.Context, *, target: utils.ChildIDConverter = None):
        """
        Lets you remove a user from being your child.
        """

        # Get the user family tree member
        family_guild_id = utils.get_family_guild_id(ctx)
        user_tree = utils.FamilyTreeMember.get(ctx.author.id, guild_id=family_guild_id)

        # If they didn't give a child, give them a dropdown
        if target is None:

            # Make a list of options
            child_options = []
            for index, child_tree in enumerate(user_tree.children):
                child_name = await utils.DiscordNameManager.fetch_name_by_id(self.bot, child_tree.id)
                child_options.append(vbu.SelectOption(label=child_name, value=f"DISOWN {child_tree.id}"))
                if index >= 25:
                    return await ctx.send(
                        (
                            "I couldn't work out which of your children you wanted to disown. "
                            "You can ping or use their ID to disown them."
                        ),
                        wait=False,
                    )

            # See if they don't have any children
            if not child_options:
                return await ctx.send("You don't have any children!", wait=False)

            # Wait for them to pick one
            components = vbu.MessageComponents(vbu.ActionRow(
                vbu.SelectMenu(custom_id="DISOWN_USER", options=child_options),
            ))
            m = await ctx.send(
                "Which of your children would you like to disown?",
                components=components,
                wait=True,
            )

            # Make our check
            def check(payload: vbu.ComponentInteractionPayload):
                if payload.message.id != m.id:
                    return False
                if payload.user.id != ctx.author.id:
                    self.bot.loop.create_task(payload.respond("You can't respond to this message!", wait=False, ephemeral=True))
                    return False
                return True
            try:
                payload = await self.bot.wait_for("component_interaction", check=check, timeout=60)
                await payload.defer_update()
                await payload.message.delete()
            except asyncio.TimeoutError:
                return await ctx.send("Timed out asking for which child you want to disown :<", wait=False)

            # Get the child's ID that they selected
            target = int(payload.values[0][len("DISOWN "):])

        # Get the family tree member objects
        child_tree = utils.FamilyTreeMember.get(target, guild_id=family_guild_id)
        child_name = await utils.DiscordNameManager.fetch_name_by_id(self.bot, child_tree.id)

        # Make sure they're actually children
        if child_tree.id not in user_tree._children:
            return await ctx.send(
                f"It doesn't look like **{utils.escape_markdown(child_name)}** is one of your children!",
                allowed_mentions=discord.AllowedMentions.none(),
                wait=False,
            )

        # See if they're sure
        try:
            result = await utils.send_proposal_message(
                ctx, ctx.author,
                f"Are you sure you want to disown **{utils.escape_markdown(child_name)}**, {ctx.author.mention}?",
                timeout_message=f"Timed out making sure you want to disown, {ctx.author.mention} :<",
                cancel_message="Alright, I've cancelled your disown!",
            )
        except Exception:
            result = None
        if result is None:
            return

        # Remove from cache
        try:
            user_tree._children.remove(child_tree.id)
        except ValueError:
            pass
        child_tree._parent = None

        # Remove from redis
        async with self.bot.redis() as re:
            await re.publish('TreeMemberUpdate', user_tree.to_json())
            await re.publish('TreeMemberUpdate', child_tree.to_json())

        # Remove from database
        async with self.bot.database() as db:
            await db(
                """DELETE FROM parents WHERE child_id=$1 AND parent_id=$2 AND guild_id=$3""",
                child_tree.id, ctx.author.id, family_guild_id,
            )

        # And we're done
        await result.ctx.send(
            f"You've successfully disowned **{utils.escape_markdown(child_name)}** :c",
            allowed_mentions=discord.AllowedMentions.none(),
            wait=False,
        )

    @vbu.command(aliases=['eman', 'runaway', 'runawayfromhome'])
    @vbu.cooldown.no_raise_cooldown(1, 3, commands.BucketType.user)
    @vbu.checks.bot_is_ready()
    @commands.guild_only()
    @vbu.bot_has_permissions(send_messages=True, add_reactions=True)
    async def emancipate(self, ctx: vbu.Context):
        """
        Removes your parent.
        """

        # Get the family tree member objects
        family_guild_id = utils.get_family_guild_id(ctx)
        user_tree = utils.FamilyTreeMember.get(ctx.author.id, guild_id=family_guild_id)

        # Make sure they're the child of the instigator
        parent_tree = user_tree.parent
        if not parent_tree:
            return await ctx.send("You don't have a parent right now :<", wait=False)

        # See if they're sure
        try:
            result = await utils.send_proposal_message(
                ctx, ctx.author,
                f"Are you sure you want to leave your parent, {ctx.author.mention}?",
                timeout_message=f"Timed out making sure you want to emancipate, {ctx.author.mention} :<",
                cancel_message="Alright, I've cancelled your emancipation!",
            )
        except Exception:
            result = None
        if result is None:
            return

        # Remove family caching
        user_tree._parent = None
        try:
            parent_tree._children.remove(ctx.author.id)
        except ValueError:
            pass

        # Ping them off over reids
        async with self.bot.redis() as re:
            await re.publish('TreeMemberUpdate', user_tree.to_json())
            await re.publish('TreeMemberUpdate', parent_tree.to_json())

        # Remove their relationship from the database
        async with self.bot.database() as db:
            await db(
                """DELETE FROM parents WHERE parent_id=$1 AND child_id=$2 AND guild_id=$3""",
                parent_tree.id, ctx.author.id, family_guild_id,
            )

        # And we're done
        parent_name = await utils.DiscordNameManager.fetch_name_by_id(self.bot, parent_tree.id)
        return await result.ctx.send(f"You no longer have **{utils.escape_markdown(parent_name)}** as a parent :c", wait=False)

    @vbu.command()
    @utils.checks.has_donator_perks("can_run_disownall")
    @vbu.cooldown.no_raise_cooldown(1, 3, commands.BucketType.user)
    @vbu.checks.bot_is_ready()
    @commands.guild_only()
    @vbu.bot_has_permissions(send_messages=True, add_reactions=True)
    async def disownall(self, ctx: vbu.Context):
        """
        Disowns all of your children.
        """

        # Get the family tree member objects
        family_guild_id = utils.get_family_guild_id(ctx)
        user_tree = utils.FamilyTreeMember.get(ctx.author.id, guild_id=family_guild_id)
        child_trees = list(user_tree.children)
        if not child_trees:
            return await ctx.send("You don't have any children to disown .-.", wait=False)

        # See if they're sure
        try:
            result = await utils.send_proposal_message(
                ctx, ctx.author,
                f"Are you sure you want to disown all your children, {ctx.author.mention}?",
                timeout_message=f"Timed out making sure you want to disownall, {ctx.author.mention} :<",
                cancel_message="Alright, I've cancelled your disownall!",
            )
        except Exception:
            result = None
        if result is None:
            return

        # Disown em
        for child in child_trees:
            child._parent = None
        user_tree._children = []

        # Save em
        async with self.bot.database() as db:
            await db(
                """DELETE FROM parents WHERE parent_id=$1 AND guild_id=$2 AND child_id=ANY($3::BIGINT[])""",
                ctx.author.id, family_guild_id, [child.id for child in child_trees],
            )

        # Redis em
        async with self.bot.redis() as re:
            for person in child_trees + [user_tree]:
                await re.publish('TreeMemberUpdate', person.to_json())

        # Output to user
        await result.ctx.send("You've sucessfully disowned all of your children :c", wait=False)

    @vbu.command(aliases=["desert", "leave", "dessert"])
    @utils.checks.has_donator_perks("can_run_abandon")
    @vbu.cooldown.no_raise_cooldown(1, 3, commands.BucketType.user)
    @vbu.checks.bot_is_ready()
    @commands.guild_only()
    @vbu.bot_has_permissions(send_messages=True, add_reactions=True)
    async def abandon(self, ctx: vbu.Context):
        """
        Completely removes you from the tree.
        """

        # Set up some variables
        family_guild_id = utils.get_family_guild_id(ctx)
        user_tree = utils.FamilyTreeMember.get(ctx.author.id, guild_id=family_guild_id)

        # See if they're sure
        try:
            result = await utils.send_proposal_message(
                ctx, ctx.author,
                f"Are you sure you want to completely abandon your family, {ctx.author.mention}? This will disown all your kids, emancipate, and divorce you",
                timeout_message=f"Timed out making sure you want to abandon your family, {ctx.author.mention} :<",
                cancel_message="Alright, I've cancelled your abandonment!",
            )
        except Exception:
            result = None
        if result is None:
            return

        # Grab the users from the cache
        parent_tree = user_tree.parent
        child_trees = list(user_tree.children)
        partner_tree = user_tree.partner

        # Remove children from cache
        for child in child_trees:
            child._parent = None
        user_tree._children = []

        # Remove parent from cache
        if parent_tree:
            user_tree._parent = None
            try:
                parent_tree._children.remove(ctx.author.id)
            except ValueError:
                pass

        # Remove partner from cache
        if partner_tree:
            user_tree._partner = None
            partner_tree._partner = None

        # Remove from database
        async with self.bot.database() as db:
            await db(
                """DELETE FROM parents WHERE parent_id=$1 AND guild_id=$2 AND child_id=ANY($3::BIGINT[])""",
                ctx.author.id, family_guild_id, [child.id for child in child_trees],
            )
            if parent_tree:
                await db(
                    """DELETE FROM parents WHERE parent_id=$1 AND child_id=$2 AND guild_id=$3""",
                    parent_tree.id, ctx.author.id, family_guild_id,
                )
            if partner_tree:
                await db(
                    """DELETE FROM marriages WHERE (user_id=$1 OR user_id=$2) AND guild_id=$3""",
                    ctx.author.id, partner_tree.id, family_guild_id,
                )

        # Remove from redis
        async with self.bot.redis() as re:
            for person in child_trees:
                await re.publish('TreeMemberUpdate', person.to_json())
            if parent_tree:
                await re.publish('TreeMemberUpdate', parent_tree.to_json())
            if partner_tree:
                await re.publish('TreeMemberUpdate', partner_tree.to_json())
            await re.publish('TreeMemberUpdate', user_tree.to_json())

        # And we're done
        await result.ctx.send(
            f"You've successfully left your family, {ctx.author.mention} :c",
            allowed_mentions=discord.AllowedMentions.none(),
            wait=False,
        )


def setup(bot: vbu.Bot):
    x = Parentage(bot)
    bot.add_cog(x)
