# -*- coding: utf-8 -*-
#
#  2020-08-03 Cornelius Kölbel <cornelius.koelbel@netknights.it>
#             Initial writeup
#
#  License:  AGPLv3
#  contact:  http://www.privacyidea.org
#
# This code is free software; you can redistribute it and/or
# modify it under the terms of the GNU AFFERO GENERAL PUBLIC LICENSE
# License as published by the Free Software Foundation; either
# version 3 of the License, or any later version.
#
# This code is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNE7SS FOR A PARTICULAR PURPOSE.  See the
# GNU AFFERO GENERAL PUBLIC LICENSE for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
"""
These are the decorator functions generic challenge respsonce mechanisms:

* PIN change

Currently the decorator is only tested in tests/test_lib_token.py
"""
import logging

from privacyidea.lib.policy import Match
from privacyidea.lib.policy import ACTION, SCOPE
from privacyidea.lib.config import get_from_config
from privacyidea.lib.crypto import hash, get_rand_digit_str
from privacyidea.models import Challenge
from privacyidea.lib.challenge import get_challenges

log = logging.getLogger(__name__)


SEED_LENGTH = 16


class CHALLENGE_TYPE(object):
    PIN_RESET = "generic_pin_reset"


def _create_pin_reset_challenge(token_obj, message, challenge_data=None):
    validity = int(get_from_config('DefaultChallengeValidityTime', 120))
    validity = int(get_from_config('PinResetChallengeValidityTime', validity))
    db_challenge = Challenge(token_obj.token.serial,
                             challenge=CHALLENGE_TYPE.PIN_RESET,
                             data=challenge_data,
                             validitytime=validity)
    db_challenge.save()
    token_obj.challenge_janitor()
    reply_dict = {}
    reply_dict["multi_challenge"] = [{"transaction_id": db_challenge.transaction_id,
                                      "message": message,
                                      "serial": token_obj.token.serial,
                                      "type": token_obj.token.tokentype}]
    reply_dict["message"] = message
    reply_dict["transaction_id"] = db_challenge.transaction_id
    reply_dict["transaction_ids"] = [db_challenge.transaction_id]

    return reply_dict


def generic_challenge_response_reset_pin(wrapped_function, *args, **kwds):
    """
    Check if the authentication was successful, but if the token needs to reset
    its PIN.

    Conditions: To do so we check for "next_pin_change" in the tokeninfo data. This
    is however easily done using token.is_pin_change().

    Policies: A policy defines, if this PIN reset functionality should be active
    at all. scope=AUTH, action=CHANGE_PIN_VIA_VALIDATE

    args are:
    :param tokenobject_list: The list of all the tokens of the user, that will be checked
    :param passw: The password presented in the authentication. We need this for the PIN reset.

    kwds are:
    :param user: The user_obj
    :param options: options dictionary containing g
    """

    # Before we call the wrapped function, we need to check, if we have a generic challenge
    # for the given transaction_id and if the token serial matches a given token
    options = kwds.get("options") or {}
    transaction_id = options.get("transaction_id")
    if transaction_id:
        challenges = get_challenges(transaction_id=transaction_id, challenge=CHALLENGE_TYPE.PIN_RESET)
        if len(challenges) == 1:
            challenge = challenges[0]
            # check if challenge matches a token and if it is valid
            token_obj = next(t for t in args[0] if t.token.serial == challenge.serial)
            if token_obj:
                # Then either verify the PIN or set the PIN the first time. The
                # PIN from the 1st was is stored in challenge.data
                if challenge.data:
                    hashedpin = challenge.data[SEED_LENGTH + 1:]
                    seed = challenge.data[0:SEED_LENGTH]
                    # Verify the password
                    if hash(args[1], seed) == hashedpin:
                        # Success, set new PIN and return success
                        # TODO Verify PIN policy
                        challenge.set_otp_status(True)
                        token_obj.set_pin(args[1])
                        token_obj.challenge_janitor()
                        g = options.get("g")
                        pinpol = Match.token(g, scope=SCOPE.ENROLL, action=ACTION.CHANGE_PIN_EVERY,
                                             token_obj=token_obj).action_values(unique=True)
                        # Set a new next_pin_change
                        if pinpol:
                            # Set a new next pin change
                            token_obj.set_next_pin_change(diff=list(pinpol)[0])
                        else:
                            # Obviously the admin removed the policy for changing pins,
                            # so we will not require to change the PIN again
                            token_obj.del_tokeninfo("next_pin_change")
                        return True, {"message": "PIN successfully set.",
                                      "serial": token_obj.token.serial}
                    else:
                        return False, {"serial": token_obj.token.serial,
                                       "message": "PINs do not match"}
                else:
                    # The PIN is presented the first time.
                    # We need to ask for a 2nd time
                    challenge.set_otp_status(True)
                    seed = get_rand_digit_str(SEED_LENGTH)
                    reply_dict = _create_pin_reset_challenge(token_obj, "Please enter the new PIN again",
                                                             "{0!s}:{1!s}".format(seed, hash(args[1], seed)))
                    return False, reply_dict

    success, reply_dict = wrapped_function(*args, **kwds)

    # After a successful authentication, we might start the PIN change process
    if success and reply_dict.get("pin_change"):
        g = options.get("g")
        # Determine the realm by the serial
        serial = reply_dict.get("serial")
        # The tokenlist can contain more than one token. So we get the matching token object
        token_obj = next(t for t in args[0] if t.token.serial == serial)
        if g and Match.token(g, scope=SCOPE.AUTH, action=ACTION.CHANGE_PIN_VIA_VALIDATE, token_obj=token_obj).any():
            reply_dict = _create_pin_reset_challenge(token_obj, "Please enter a new PIN")
            return False, reply_dict

    return success, reply_dict
