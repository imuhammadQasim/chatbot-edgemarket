<!-- NEEDS TEAM REVIEW -->
<!-- Drafted from edgemarket_2: README "Key concepts", Tokenomics/tokenLayers.js, CreateMarket/CreateMarketPage.jsx,
     Statistics/GetStatistics.jsx, ValidateResultDetails, hooks/useMarketState.js, HowItsWork/ClaimWinnings.
     The HowItsWork screens and Terms page still describe the older MetaBet product; only points that match the
     current code were kept. Anything the bot is unsure of should send the user to the UI, not be guessed. -->

# EdgeMarket guide

EdgeMarket is an AI-assisted prediction market. Each market asks a factual question about a future event
(for example "Will X happen by 10 October?") with two or more outcomes. People back the outcome they think
will happen; after the market closes, the community validates what actually happened and winners are paid.

## Tokens
- **SIGNAL**: the Liquidity & Settlement Layer token on BNB Smart Chain. It is the on-chain asset used to place
  predictions, provide liquidity, create markets and earn ecosystem rewards. Fixed supply of 1,000,000,000.
- **SIGNAL**: the Truth Layer (validation) token. It powers validation, voting, reputation and rewards for
  validators, and is also used for off-chain predictions (for example through Telegram).
- **Edge ID**: EdgeMarket's identity NFT.
- Token prices, supply schedules and listings change; for exact figures send users to the Tokenomics page.
  Never quote a token price or predict one.

## Accounts and wallets
- **EVM wallets** (for SIGNAL on BNB Smart Chain) connect with the **Connect Wallet** button.
- **TON wallets** connect through TON Connect.
- Some actions need a linked **Email, Telegram or X** account (the site says "Connect your wallet, Email,
  Telegram or X to predict"). Validator rewards are delivered over Telegram, so validators should connect
  Telegram.
- EdgeMarket never needs a seed phrase or private key. Anyone asking for one is a scammer.

## Reading a market page
- **Market Confidence**: the share of participants backing each outcome.
- **Market Liquidity**: the share of the total staked amount placed on each outcome.
- **Prediction Ends In**: the countdown to the market's close. After that, no new predictions are accepted.
- The two percentages can differ: many small predictions raise Confidence, a few large ones raise Liquidity.

## Placing a prediction (SIGNAL)
1. Open the market and connect your wallet.
2. Under **Place Your Prediction**, pick an outcome and enter an amount (it must be greater than 0).
3. The first time, approve SIGNAL: the wallet asks you to confirm an approval transaction
   ("SIGNAL approved successfully").
4. Confirm the prediction transaction in your wallet. You need a little BNB for gas.
5. When it confirms you will see "Prediction placed". Your predictions appear under **Positions**.
- If the market has ended you will see "Event has ended. You cannot place bets on this event."

## How payouts work
- All predictions on a market form a pool. Winners share it in proportion to how much they contributed to
  the winning side, so a payout depends on how others predicted and is never fixed in advance.
- Track active predictions under **Positions** (your prediction history). After a market is resolved, claim
  winnings there with your wallet connected.

## Validation and settlement
- After a market closes it waits for its **validation window** ("Awaiting Validation").
- When the window opens the market is **In Resolution**: validators answer the market's validation question
  on the **Validate** page using objective evidence ("Don't just predict what happens. Verify what happened").
  There is a limit on how many validations one person can submit.
- Correct validators earn **validator rewards**: a share of 1% of everything predicted on that market,
  paid in off-chain SIGNAL over Telegram.
- When the result is published the market is **Resolved** and shows its winning outcome. A market can also be
  ended early by an admin, or refunded, in which case stakes are returned.

## Creating a market
Use **Create market** ("Define your prediction, set the rules, and publish it permissionlessly"):
1. **Define your prediction**: a factual market question, an objective validation question validators can
   verify with evidence, and two or more unique outcomes.
2. **Configure rules & economics**: category, closing time (shown in local time and UTC), accepted tokens
   (SIGNAL and/or off-chain SIGNA), creator and referral rewards (maximum 5% each), and the wallets or Telegram IDs that
   receive them.
3. **Ready to launch**: review the checklist and costs (contract deployment gas, validation protocol,
   creator/referral setup), confirm the market follows the EdgeMarket Protocol, and deploy it on-chain with
   your wallet. User-submitted markets are reviewed by the EdgeMarket team before they go live.

## Rules and safety
- Users must be at least 18 and allowed to use crypto prediction markets in their jurisdiction.
- Nothing on EdgeMarket or from this assistant is financial advice. Outcomes are never guaranteed and
  people can lose what they stake.
- The assistant cannot place predictions, sign transactions or access wallets.
