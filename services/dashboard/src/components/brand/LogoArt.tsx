/* Generated from docs/assets/logo-mark.svg and the animated footer logo in docs/index.html. Colours are the
 * --logo-* tokens (logo.css); ids are prefixed per instance so several logos can share a page. */
import type { CSSProperties } from "react";

/** Stagger index read by the assemble/live keyframes (calc() with var(--i)). */
const idx = (i: number) => ({ "--i": i }) as CSSProperties;

export function LogoArt({ p, animated = false }: { p: string; animated?: boolean }) {
	return (
		<>
			<defs>
				<linearGradient id={`${p}grad-band`} gradientUnits="userSpaceOnUse" x1="60" y1="60" x2="450" y2="470">
					<stop offset="0" style={{ stopColor: "var(--logo-white)" }} />
					<stop offset="0.16" style={{ stopColor: "var(--logo-frost)" }} />
					<stop offset="0.27" style={{ stopColor: "var(--logo-white)" }} />
					<stop offset="0.275" style={{ stopColor: "var(--logo-steel)" }} />
					<stop offset="0.36" style={{ stopColor: "var(--logo-mist)" }} />
					<stop offset="0.46" style={{ stopColor: "var(--logo-ice)" }} />
					<stop offset="0.5" style={{ stopColor: "var(--logo-white)" }} />
					<stop offset="0.505" style={{ stopColor: "var(--logo-cyan)" }} />
					<stop offset="0.62" style={{ stopColor: "var(--logo-cyandeep)" }} />
					<stop offset="0.72" style={{ stopColor: "var(--logo-azure)" }} />
					<stop offset="0.76" style={{ stopColor: "var(--logo-band)" }} />
					<stop offset="0.765" style={{ stopColor: "var(--logo-cobalt)" }} />
					<stop offset="1" style={{ stopColor: "var(--logo-navy)" }} />
				</linearGradient>
				<linearGradient id={`${p}grad-side`} gradientUnits="userSpaceOnUse" x1="0" y1="60" x2="0" y2="480">
					<stop offset="0" style={{ stopColor: "var(--logo-side)" }} />
					<stop offset="1" style={{ stopColor: "var(--logo-sidedeep)" }} />
				</linearGradient>
				<linearGradient id={`${p}grad-bevel-out`} gradientUnits="userSpaceOnUse" x1="70" y1="60" x2="440" y2="470">
					<stop offset="0" style={{ stopColor: "var(--logo-white)", stopOpacity: 0.95 }} />
					<stop offset="0.5" style={{ stopColor: "var(--logo-white)", stopOpacity: 0.35 }} />
					<stop offset="1" style={{ stopColor: "var(--logo-sidedeep)", stopOpacity: 0.7 }} />
				</linearGradient>
				<linearGradient id={`${p}grad-bevel-in`} gradientUnits="userSpaceOnUse" x1="100" y1="95" x2="400" y2="425">
					<stop offset="0" style={{ stopColor: "var(--logo-sidedeep)", stopOpacity: 0.85 }} />
					<stop offset="0.55" style={{ stopColor: "var(--logo-navy)", stopOpacity: 0.4 }} />
					<stop offset="1" style={{ stopColor: "var(--logo-ice)", stopOpacity: 0.75 }} />
				</linearGradient>
				<radialGradient id={`${p}grad-well`} cx=".45" cy=".42" r=".62">
					<stop offset="0" style={{ stopColor: "var(--logo-well)" }} />
					<stop offset="1" style={{ stopColor: "var(--logo-welldeep)" }} />
				</radialGradient>
				<linearGradient id={`${p}grad-block`} gradientUnits="userSpaceOnUse" x1="110" y1="120" x2="300" y2="410">
					<stop offset="0" style={{ stopColor: "var(--logo-bhi)" }} />
					<stop offset="0.2" style={{ stopColor: "var(--logo-bcyan)" }} />
					<stop offset="0.34" style={{ stopColor: "var(--logo-bcyan2)" }} />
					<stop offset="0.345" style={{ stopColor: "var(--logo-bband)" }} />
					<stop offset="0.38" style={{ stopColor: "var(--logo-bcyan3)" }} />
					<stop offset="0.62" style={{ stopColor: "var(--logo-bmid)" }} />
					<stop offset="0.625" style={{ stopColor: "var(--logo-bband2)" }} />
					<stop offset="0.66" style={{ stopColor: "var(--logo-bblue)" }} />
					<stop offset="1" style={{ stopColor: "var(--logo-bdeep)" }} />
				</linearGradient>
				<linearGradient id={`${p}grad-bevel-block`} gradientUnits="userSpaceOnUse" x1="110" y1="120" x2="300" y2="410">
					<stop offset="0" style={{ stopColor: "var(--logo-white)", stopOpacity: 0.9 }} />
					<stop offset="0.5" style={{ stopColor: "var(--logo-white)", stopOpacity: 0.25 }} />
					<stop offset="1" style={{ stopColor: "var(--logo-sidedeep)", stopOpacity: 0.6 }} />
				</linearGradient>
				<linearGradient id={`${p}grad-pipe`} gradientUnits="userSpaceOnUse" x1="300" y1="190" x2="430" y2="330">
					<stop offset="0" style={{ stopColor: "var(--logo-pipe)" }} />
					<stop offset="1" style={{ stopColor: "var(--logo-pipe2)" }} />
				</linearGradient>
				<radialGradient id={`${p}grad-bolt`} cx=".35" cy=".3" r=".8">
					<stop offset="0" style={{ stopColor: "var(--logo-white)" }} />
					<stop offset="0.45" style={{ stopColor: "var(--logo-steel)" }} />
					<stop offset="1" style={{ stopColor: "var(--logo-navy)" }} />
				</radialGradient>
				<linearGradient id={`${p}grad-collar`} gradientUnits="userSpaceOnUse" x1="0" y1="-11" x2="0" y2="11">
					<stop offset="0" style={{ stopColor: "var(--logo-white)" }} />
					<stop offset="0.35" style={{ stopColor: "var(--logo-steel)" }} />
					<stop offset="0.36" style={{ stopColor: "var(--logo-azure)" }} />
					<stop offset="1" style={{ stopColor: "var(--logo-navy)" }} />
				</linearGradient>
				<radialGradient id={`${p}grad-sphere`} cx=".38" cy=".32" r=".78">
					<stop offset="0" style={{ stopColor: "var(--logo-shi)" }} />
					<stop offset="0.18" style={{ stopColor: "var(--logo-sice)" }} />
					<stop offset="0.46" style={{ stopColor: "var(--logo-score)" }} />
					<stop offset="0.78" style={{ stopColor: "var(--logo-smid)" }} />
					<stop offset="1" style={{ stopColor: "var(--logo-sedge)" }} />
				</radialGradient>
				<radialGradient id={`${p}grad-core`} cx=".56" cy=".62" r=".5">
					<stop offset="0" style={{ stopColor: "var(--logo-sice)", stopOpacity: 0.8 }} />
					<stop offset="1" style={{ stopColor: "var(--logo-score)", stopOpacity: 0 }} />
				</radialGradient>
				<linearGradient id={`${p}grad-rim`} gradientUnits="objectBoundingBox" x1="0" y1="0" x2="1" y2="1">
					<stop offset="0" style={{ stopColor: "var(--logo-white)", stopOpacity: 0 }} />
					<stop offset="0.55" style={{ stopColor: "var(--logo-white)", stopOpacity: 0 }} />
					<stop offset="1" style={{ stopColor: "var(--logo-ice)", stopOpacity: 0.95 }} />
				</linearGradient>
				<pattern id={`${p}brush`} patternUnits="userSpaceOnUse" width="5" height="3" patternTransform="rotate(-24)">
					<rect width="5" height="1" style={{ fill: "var(--logo-white)" }} opacity=".07" />
				</pattern>
				<clipPath id={`${p}clip-sphere`}>
					<circle cx="452" cy="273" r="33" />
				</clipPath>
				<radialGradient id={`${p}grad-bloom`}>
					<stop offset="0" style={{ stopColor: "var(--logo-score)", stopOpacity: 0.9 }} />
					<stop offset="0.35" style={{ stopColor: "var(--logo-score)", stopOpacity: 0.45 }} />
					<stop offset="0.7" style={{ stopColor: "var(--logo-score)", stopOpacity: 0.12 }} />
					<stop offset="1" style={{ stopColor: "var(--logo-score)", stopOpacity: 0 }} />
				</radialGradient>
				{animated && (
					<linearGradient id={`${p}grad-sweep`} x1="0" y1="0" x2="1" y2="0">
						<stop offset="0" style={{ stopColor: "var(--logo-white)", stopOpacity: 0 }} />
						<stop offset="0.5" style={{ stopColor: "var(--logo-white)", stopOpacity: 0.55 }} />
						<stop offset="1" style={{ stopColor: "var(--logo-white)", stopOpacity: 0 }} />
					</linearGradient>
				)}
				{animated && (
					<mask id={`${p}mask-chrome`} maskUnits="userSpaceOnUse" x="0" y="0" width="512" height="512">
						<path
							d="M418 238 V165 L250 70 L82 165 V260 M82 260 V355 L250 450 L418 355 V306"
							fill="none"
							stroke="#fff"
							strokeWidth="40"
							strokeLinejoin="round"
						/>
						<path
							d="M127 188 L224 136 Q240 128 245 146 L249 164 Q255 186 282 187 L300 187 V201 L258 201 L201 236 Q190 243 178 237 L126 207 Q112 198 127 188 Z"
							fill="#fff"
						/>
						<path
							d="M116 222 L186 250 Q192 252 198 248 L226 233 Q238 227 246 238 Q262 262 284 265 L300 265 V283 L284 283 Q262 285 247 306 Q238 318 226 311 L198 296 Q192 293 186 296 L118 324 Q108 328 108 316 V230 Q108 219 116 222 Z"
							fill="#fff"
						/>
						<path
							d="M126 344 L186 314 Q195 309 204 315 L254 346 L300 346 V360 L282 360 Q256 362 249 378 L240 402 Q234 414 222 408 L126 360 Q112 352 126 344 Z"
							fill="#fff"
						/>
					</mask>
				)}
			</defs>

			{animated && (
				<g className="lm-edge">
					<path
						d="M418 238 V165 L250 70 L82 165 V260 M82 260 V355 L250 450 L418 355 V306"
						strokeWidth="76"
						opacity=".35"
					/>
					<path
						d="M418 238 V165 L250 70 L82 165 V260 M82 260 V355 L250 450 L418 355 V306"
						strokeWidth="60"
						opacity=".6"
					/>
					<path d="M418 238 V165 L250 70 L82 165 V260 M82 260 V355 L250 450 L418 355 V306" strokeWidth="48" />
				</g>
			)}
			<path
				className="lm-well"
				d="M398 176.6 L250 93 L102 176.6 V343.4 L250 427 L398 343.4 Z"
				fill={`url(#${p}grad-well)`}
			/>
			<path
				className="lm-well-ao"
				d="M398 238 V176.6 L250 93 L102 176.6 V343.4 L250 427 L398 343.4 V306"
				fill="none"
				style={{ stroke: "var(--logo-welldeep)" }}
				strokeWidth="16"
				opacity=".35"
			/>
			<path
				className="lm-well-ao"
				d="M398 238 V176.6 L250 93 L102 176.6 V343.4 L250 427 L398 343.4 V306"
				fill="none"
				style={{ stroke: "var(--logo-welldeep)" }}
				strokeWidth="8"
				opacity=".6"
			/>
			<g className="lm-frame">
				<g className="lm-frame-side" transform="translate(6 8)">
					<path
						className="lm-frame-sideband lm-frame-upper"
						pathLength="1"
						d="M418 238 V165 L250 70 L82 165 V260"
						fill="none"
						stroke={`url(#${p}grad-side)`}
						strokeWidth="40"
						strokeLinejoin="round"
					/>
					<path
						className="lm-frame-sideband lm-frame-lower"
						pathLength="1"
						d="M82 260 V355 L250 450 L418 355 V306"
						fill="none"
						stroke={`url(#${p}grad-side)`}
						strokeWidth="40"
						strokeLinejoin="round"
					/>
				</g>
				<g className="lm-frame-face">
					<path
						className="lm-frame-band lm-frame-upper"
						pathLength="1"
						d="M418 238 V165 L250 70 L82 165 V260"
						fill="none"
						stroke={`url(#${p}grad-band)`}
						strokeWidth="40"
						strokeLinejoin="round"
					/>
					<path
						className="lm-frame-band lm-frame-lower"
						pathLength="1"
						d="M82 260 V355 L250 450 L418 355 V306"
						fill="none"
						stroke={`url(#${p}grad-band)`}
						strokeWidth="40"
						strokeLinejoin="round"
					/>
				</g>
				<g className="lm-frame-detail">
					<path
						className="lm-frame-bevel-out"
						d="M438 238 V153.4 L250 47 L62 153.4 V366.6 L250 473 L438 366.6 V306"
						fill="none"
						stroke={`url(#${p}grad-bevel-out)`}
						strokeWidth="2"
						strokeLinejoin="round"
					/>
					<path
						className="lm-frame-bevel-in"
						d="M398 238 V176.6 L250 93 L102 176.6 V343.4 L250 427 L398 343.4 V306"
						fill="none"
						stroke={`url(#${p}grad-bevel-in)`}
						strokeWidth="2.2"
						strokeLinejoin="round"
					/>
					<path
						className="lm-frame-lip"
						d="M398 238 V176.6 L250 93 L102 176.6 V343.4 L250 427 L398 343.4 V306"
						transform="translate(3 3)"
						fill="none"
						style={{ stroke: "var(--logo-white)" }}
						strokeOpacity=".22"
						strokeWidth="1"
					/>
					<path
						className="lm-frame-shade"
						d="M400 232 V176 L262 98"
						fill="none"
						style={{ stroke: "var(--logo-navy)" }}
						strokeOpacity=".6"
						strokeWidth="4"
						strokeLinecap="round"
						strokeLinejoin="round"
					/>
					<path
						className="lm-frame-gloss"
						d="M70 168 L244 70"
						fill="none"
						style={{ stroke: "var(--logo-white)" }}
						strokeWidth="3"
						strokeLinecap="round"
					/>
					<g className="lm-frame-seams">
						<path
							d="M61 260 H103"
							fill="none"
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeOpacity=".55"
							strokeWidth="2"
						/>
						<path
							d="M61 260 H103"
							transform="translate(1.5 1.5)"
							fill="none"
							style={{ stroke: "var(--logo-white)" }}
							strokeOpacity=".45"
							strokeWidth="1"
						/>
						<path
							d="M156 99.5 L176 135.5"
							fill="none"
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeOpacity=".55"
							strokeWidth="2"
						/>
						<path
							d="M156 99.5 L176 135.5"
							transform="translate(1.5 1.5)"
							fill="none"
							style={{ stroke: "var(--logo-white)" }}
							strokeOpacity=".45"
							strokeWidth="1"
						/>
						<path
							d="M156 420.5 L176 384.5"
							fill="none"
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeOpacity=".55"
							strokeWidth="2"
						/>
						<path
							d="M156 420.5 L176 384.5"
							transform="translate(1.5 1.5)"
							fill="none"
							style={{ stroke: "var(--logo-white)" }}
							strokeOpacity=".45"
							strokeWidth="1"
						/>
						<path
							d="M324 135.5 L344 99.5"
							fill="none"
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeOpacity=".55"
							strokeWidth="2"
						/>
						<path
							d="M324 135.5 L344 99.5"
							transform="translate(1.5 1.5)"
							fill="none"
							style={{ stroke: "var(--logo-white)" }}
							strokeOpacity=".45"
							strokeWidth="1"
						/>
						<path
							d="M324 384.5 L344 420.5"
							fill="none"
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeOpacity=".55"
							strokeWidth="2"
						/>
						<path
							d="M324 384.5 L344 420.5"
							transform="translate(1.5 1.5)"
							fill="none"
							style={{ stroke: "var(--logo-white)" }}
							strokeOpacity=".45"
							strokeWidth="1"
						/>
					</g>
					<g className="lm-frame-bolts">
						<circle
							cx="250"
							cy="70"
							r="6"
							fill={`url(#${p}grad-bolt)`}
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeWidth="1.5"
						/>
						<path
							d="M247 70 H253"
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeOpacity=".7"
							strokeWidth="1.2"
							strokeLinecap="round"
						/>
						<circle
							cx="82"
							cy="165"
							r="6"
							fill={`url(#${p}grad-bolt)`}
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeWidth="1.5"
						/>
						<path
							d="M79 165 H85"
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeOpacity=".7"
							strokeWidth="1.2"
							strokeLinecap="round"
						/>
						<circle
							cx="82"
							cy="355"
							r="6"
							fill={`url(#${p}grad-bolt)`}
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeWidth="1.5"
						/>
						<path
							d="M79 355 H85"
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeOpacity=".7"
							strokeWidth="1.2"
							strokeLinecap="round"
						/>
						<circle
							cx="250"
							cy="450"
							r="6"
							fill={`url(#${p}grad-bolt)`}
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeWidth="1.5"
						/>
						<path
							d="M247 450 H253"
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeOpacity=".7"
							strokeWidth="1.2"
							strokeLinecap="round"
						/>
						<circle
							cx="418"
							cy="165"
							r="6"
							fill={`url(#${p}grad-bolt)`}
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeWidth="1.5"
						/>
						<path
							d="M415 165 H421"
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeOpacity=".7"
							strokeWidth="1.2"
							strokeLinecap="round"
						/>
						<circle
							cx="418"
							cy="355"
							r="6"
							fill={`url(#${p}grad-bolt)`}
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeWidth="1.5"
						/>
						<path
							d="M415 355 H421"
							style={{ stroke: "var(--logo-sidedeep)" }}
							strokeOpacity=".7"
							strokeWidth="1.2"
							strokeLinecap="round"
						/>
					</g>
				</g>
			</g>
			<g className="lm-channels">
				<g className="lm-channel-top lm-pipe" style={idx(0)}>
					<path
						className="lm-pipe-shell"
						pathLength="1"
						d="M297 194 H312 Q324 194 332 204 L368 252 Q378 266 392 274"
						fill="none"
						style={{ stroke: "var(--logo-pipedark)" }}
						strokeWidth="17"
						strokeLinecap="round"
						strokeLinejoin="round"
					/>
					<path
						className="lm-pipe-body"
						pathLength="1"
						d="M297 194 H312 Q324 194 332 204 L368 252 Q378 266 392 274"
						fill="none"
						stroke={`url(#${p}grad-pipe)`}
						strokeWidth="11"
						strokeLinecap="round"
						strokeLinejoin="round"
					/>
					<path
						className="lm-pipe-hi"
						pathLength="1"
						d="M297 194 H312 Q324 194 332 204 L368 252 Q378 266 392 274"
						fill="none"
						style={{ stroke: "var(--logo-pipehi)" }}
						strokeOpacity=".75"
						strokeWidth="3"
						strokeLinecap="round"
						strokeLinejoin="round"
						transform="translate(0 -3)"
					/>
				</g>
				<g className="lm-channel-mid lm-pipe" style={idx(1)}>
					<path
						className="lm-pipe-shell"
						pathLength="1"
						d="M297 274 H392"
						fill="none"
						style={{ stroke: "var(--logo-pipedark)" }}
						strokeWidth="17"
						strokeLinecap="round"
						strokeLinejoin="round"
					/>
					<path
						className="lm-pipe-body"
						pathLength="1"
						d="M297 274 H392"
						fill="none"
						stroke={`url(#${p}grad-pipe)`}
						strokeWidth="11"
						strokeLinecap="round"
						strokeLinejoin="round"
					/>
					<path
						className="lm-pipe-hi"
						pathLength="1"
						d="M297 274 H392"
						fill="none"
						style={{ stroke: "var(--logo-pipehi)" }}
						strokeOpacity=".75"
						strokeWidth="3"
						strokeLinecap="round"
						strokeLinejoin="round"
						transform="translate(0 -3)"
					/>
				</g>
				<g className="lm-channel-bottom lm-pipe" style={idx(2)}>
					<path
						className="lm-pipe-shell"
						pathLength="1"
						d="M297 353 H312 Q324 353 332 343 L368 295 Q378 281 392 274"
						fill="none"
						style={{ stroke: "var(--logo-pipedark)" }}
						strokeWidth="17"
						strokeLinecap="round"
						strokeLinejoin="round"
					/>
					<path
						className="lm-pipe-body"
						pathLength="1"
						d="M297 353 H312 Q324 353 332 343 L368 295 Q378 281 392 274"
						fill="none"
						stroke={`url(#${p}grad-pipe)`}
						strokeWidth="11"
						strokeLinecap="round"
						strokeLinejoin="round"
					/>
					<path
						className="lm-pipe-hi"
						pathLength="1"
						d="M297 353 H312 Q324 353 332 343 L368 295 Q378 281 392 274"
						fill="none"
						style={{ stroke: "var(--logo-pipehi)" }}
						strokeOpacity=".75"
						strokeWidth="3"
						strokeLinecap="round"
						strokeLinejoin="round"
						transform="translate(0 -3)"
					/>
				</g>
				<g className="lm-merge lm-pipe" style={idx(3)}>
					<path
						className="lm-pipe-shell"
						pathLength="1"
						d="M392 274 H420"
						fill="none"
						style={{ stroke: "var(--logo-pipedark)" }}
						strokeWidth="17"
						strokeLinecap="round"
					/>
					<path
						className="lm-pipe-body"
						pathLength="1"
						d="M392 274 H420"
						fill="none"
						stroke={`url(#${p}grad-pipe)`}
						strokeWidth="11"
						strokeLinecap="round"
					/>
					<path
						className="lm-pipe-hi"
						pathLength="1"
						d="M392 274 H420"
						fill="none"
						style={{ stroke: "var(--logo-pipehi)" }}
						strokeOpacity=".75"
						strokeWidth="3"
						strokeLinecap="round"
						transform="translate(0 -3)"
					/>
				</g>
				<g className="lm-couplings">
					<rect
						x="295"
						y="-11"
						width="10"
						height="22"
						rx="3"
						transform="translate(0 194)"
						fill={`url(#${p}grad-collar)`}
						style={{ stroke: "var(--logo-pipedark)" }}
						strokeWidth="1.5"
					/>
					<rect
						x="295"
						y="-11"
						width="10"
						height="22"
						rx="3"
						transform="translate(0 274)"
						fill={`url(#${p}grad-collar)`}
						style={{ stroke: "var(--logo-pipedark)" }}
						strokeWidth="1.5"
					/>
					<rect
						x="295"
						y="-11"
						width="10"
						height="22"
						rx="3"
						transform="translate(0 353)"
						fill={`url(#${p}grad-collar)`}
						style={{ stroke: "var(--logo-pipedark)" }}
						strokeWidth="1.5"
					/>
					<rect
						x="409"
						y="-11"
						width="10"
						height="22"
						rx="3"
						transform="translate(0 274)"
						fill={`url(#${p}grad-collar)`}
						style={{ stroke: "var(--logo-pipedark)" }}
						strokeWidth="1.5"
					/>
				</g>
			</g>
			<g className="lm-block-top" style={idx(0)}>
				<path
					className="lm-block-shadow"
					d="M127 188 L224 136 Q240 128 245 146 L249 164 Q255 186 282 187 L300 187 V201 L258 201 L201 236 Q190 243 178 237 L126 207 Q112 198 127 188 Z"
					style={{ fill: "var(--logo-shadow)" }}
					opacity=".22"
					transform="translate(13 17)"
				/>
				<path
					className="lm-block-shadow"
					d="M127 188 L224 136 Q240 128 245 146 L249 164 Q255 186 282 187 L300 187 V201 L258 201 L201 236 Q190 243 178 237 L126 207 Q112 198 127 188 Z"
					style={{ fill: "var(--logo-shadow)" }}
					opacity=".32"
					transform="translate(9 12)"
				/>
				<path
					className="lm-block-side"
					d="M127 188 L224 136 Q240 128 245 146 L249 164 Q255 186 282 187 L300 187 V201 L258 201 L201 236 Q190 243 178 237 L126 207 Q112 198 127 188 Z"
					fill={`url(#${p}grad-side)`}
					transform="translate(5 7)"
				/>
				<path
					className="lm-block-face"
					d="M127 188 L224 136 Q240 128 245 146 L249 164 Q255 186 282 187 L300 187 V201 L258 201 L201 236 Q190 243 178 237 L126 207 Q112 198 127 188 Z"
					fill={`url(#${p}grad-block)`}
				/>
				<path
					className="lm-block-brush"
					d="M127 188 L224 136 Q240 128 245 146 L249 164 Q255 186 282 187 L300 187 V201 L258 201 L201 236 Q190 243 178 237 L126 207 Q112 198 127 188 Z"
					fill={`url(#${p}brush)`}
				/>
				<path
					className="lm-block-bevel"
					d="M127 188 L224 136 Q240 128 245 146 L249 164 Q255 186 282 187 L300 187 V201 L258 201 L201 236 Q190 243 178 237 L126 207 Q112 198 127 188 Z"
					fill="none"
					stroke={`url(#${p}grad-bevel-block)`}
					strokeWidth="1.6"
					strokeLinejoin="round"
				/>
				<path
					className="lm-block-shine"
					d="M131 185 L222 138"
					fill="none"
					style={{ stroke: "var(--logo-white)" }}
					strokeOpacity=".75"
					strokeWidth="2.4"
					strokeLinecap="round"
				/>
				{animated && <path className="lm-lockflash" d="M301 186 V202" />}
			</g>
			<g className="lm-block-mid" style={idx(1)}>
				<path
					className="lm-block-shadow"
					d="M116 222 L186 250 Q192 252 198 248 L226 233 Q238 227 246 238 Q262 262 284 265 L300 265 V283 L284 283 Q262 285 247 306 Q238 318 226 311 L198 296 Q192 293 186 296 L118 324 Q108 328 108 316 V230 Q108 219 116 222 Z"
					style={{ fill: "var(--logo-shadow)" }}
					opacity=".22"
					transform="translate(13 17)"
				/>
				<path
					className="lm-block-shadow"
					d="M116 222 L186 250 Q192 252 198 248 L226 233 Q238 227 246 238 Q262 262 284 265 L300 265 V283 L284 283 Q262 285 247 306 Q238 318 226 311 L198 296 Q192 293 186 296 L118 324 Q108 328 108 316 V230 Q108 219 116 222 Z"
					style={{ fill: "var(--logo-shadow)" }}
					opacity=".32"
					transform="translate(9 12)"
				/>
				<path
					className="lm-block-side"
					d="M116 222 L186 250 Q192 252 198 248 L226 233 Q238 227 246 238 Q262 262 284 265 L300 265 V283 L284 283 Q262 285 247 306 Q238 318 226 311 L198 296 Q192 293 186 296 L118 324 Q108 328 108 316 V230 Q108 219 116 222 Z"
					fill={`url(#${p}grad-side)`}
					transform="translate(5 7)"
				/>
				<path
					className="lm-block-face"
					d="M116 222 L186 250 Q192 252 198 248 L226 233 Q238 227 246 238 Q262 262 284 265 L300 265 V283 L284 283 Q262 285 247 306 Q238 318 226 311 L198 296 Q192 293 186 296 L118 324 Q108 328 108 316 V230 Q108 219 116 222 Z"
					fill={`url(#${p}grad-block)`}
				/>
				<path
					className="lm-block-brush"
					d="M116 222 L186 250 Q192 252 198 248 L226 233 Q238 227 246 238 Q262 262 284 265 L300 265 V283 L284 283 Q262 285 247 306 Q238 318 226 311 L198 296 Q192 293 186 296 L118 324 Q108 328 108 316 V230 Q108 219 116 222 Z"
					fill={`url(#${p}brush)`}
				/>
				<path
					className="lm-block-bevel"
					d="M116 222 L186 250 Q192 252 198 248 L226 233 Q238 227 246 238 Q262 262 284 265 L300 265 V283 L284 283 Q262 285 247 306 Q238 318 226 311 L198 296 Q192 293 186 296 L118 324 Q108 328 108 316 V230 Q108 219 116 222 Z"
					fill="none"
					stroke={`url(#${p}grad-bevel-block)`}
					strokeWidth="1.6"
					strokeLinejoin="round"
				/>
				<path
					className="lm-block-shine"
					d="M117 226 L182 252"
					fill="none"
					style={{ stroke: "var(--logo-white)" }}
					strokeOpacity=".75"
					strokeWidth="2.4"
					strokeLinecap="round"
				/>
				{animated && <path className="lm-lockflash" d="M301 264 V284" />}
			</g>
			<g className="lm-block-bottom" style={idx(2)}>
				<path
					className="lm-block-shadow"
					d="M126 344 L186 314 Q195 309 204 315 L254 346 L300 346 V360 L282 360 Q256 362 249 378 L240 402 Q234 414 222 408 L126 360 Q112 352 126 344 Z"
					style={{ fill: "var(--logo-shadow)" }}
					opacity=".22"
					transform="translate(13 17)"
				/>
				<path
					className="lm-block-shadow"
					d="M126 344 L186 314 Q195 309 204 315 L254 346 L300 346 V360 L282 360 Q256 362 249 378 L240 402 Q234 414 222 408 L126 360 Q112 352 126 344 Z"
					style={{ fill: "var(--logo-shadow)" }}
					opacity=".32"
					transform="translate(9 12)"
				/>
				<path
					className="lm-block-side"
					d="M126 344 L186 314 Q195 309 204 315 L254 346 L300 346 V360 L282 360 Q256 362 249 378 L240 402 Q234 414 222 408 L126 360 Q112 352 126 344 Z"
					fill={`url(#${p}grad-side)`}
					transform="translate(5 7)"
				/>
				<path
					className="lm-block-face"
					d="M126 344 L186 314 Q195 309 204 315 L254 346 L300 346 V360 L282 360 Q256 362 249 378 L240 402 Q234 414 222 408 L126 360 Q112 352 126 344 Z"
					fill={`url(#${p}grad-block)`}
				/>
				<path
					className="lm-block-brush"
					d="M126 344 L186 314 Q195 309 204 315 L254 346 L300 346 V360 L282 360 Q256 362 249 378 L240 402 Q234 414 222 408 L126 360 Q112 352 126 344 Z"
					fill={`url(#${p}brush)`}
				/>
				<path
					className="lm-block-bevel"
					d="M126 344 L186 314 Q195 309 204 315 L254 346 L300 346 V360 L282 360 Q256 362 249 378 L240 402 Q234 414 222 408 L126 360 Q112 352 126 344 Z"
					fill="none"
					stroke={`url(#${p}grad-bevel-block)`}
					strokeWidth="1.6"
					strokeLinejoin="round"
				/>
				<path
					className="lm-block-shine"
					d="M130 342 L186 316"
					fill="none"
					style={{ stroke: "var(--logo-white)" }}
					strokeOpacity=".75"
					strokeWidth="2.4"
					strokeLinecap="round"
				/>
				{animated && <path className="lm-lockflash" d="M301 345 V361" />}
			</g>
			{animated && (
				<g className="lm-sweep-wrap" mask={`url(#${p}mask-chrome)`}>
					<g transform="rotate(22 256 256)">
						<rect className="lm-sweep" x="-60" y="-200" width="110" height="920" fill={`url(#${p}grad-sweep)`} />
					</g>
				</g>
			)}
			{animated && (
				<g className="lm-pulses">
					<path
						className="lm-pulse"
						pathLength="1"
						style={idx(0)}
						d="M297 194 H312 Q324 194 332 204 L368 252 Q378 266 392 274 H421"
					/>
					<path className="lm-pulse" pathLength="1" style={idx(1)} d="M297 274 H421" />
					<path
						className="lm-pulse"
						pathLength="1"
						style={idx(2)}
						d="M297 353 H312 Q324 353 332 343 L368 295 Q378 281 392 274 H421"
					/>
				</g>
			)}
			<circle className="lm-bloom" cx="452" cy="273" r="66" fill={`url(#${p}grad-bloom)`} />
			<g className="lm-sphere">
				<circle className="lm-sphere-core" cx="452" cy="273" r="33" fill={`url(#${p}grad-sphere)`} />
				<circle className="lm-sphere-glow" cx="452" cy="273" r="22" fill={`url(#${p}grad-core)`} />
				<g clipPath={`url(#${p}clip-sphere)`}>
					<ellipse
						className="lm-sphere-reflect"
						cx="454"
						cy="290"
						rx="35"
						ry="8"
						style={{ fill: "var(--logo-white)" }}
						opacity=".2"
					/>
				</g>
				<circle
					className="lm-sphere-rim"
					cx="452"
					cy="273"
					r="32"
					fill="none"
					stroke={`url(#${p}grad-rim)`}
					strokeWidth="2"
				/>
				<ellipse
					className="lm-sphere-highlight"
					cx="441"
					cy="259"
					rx="9"
					ry="5.5"
					transform="rotate(-30 441 259)"
					style={{ fill: "var(--logo-white)" }}
				/>
				<circle className="lm-sphere-glint" cx="437" cy="256" r="2" style={{ fill: "var(--logo-white)" }} />
			</g>
			{animated && (
				<g className="lm-flare">
					<ellipse cx="452" cy="273" rx="96" ry="1.8" />
					<ellipse cx="452" cy="273" rx="1.8" ry="44" />
					<circle cx="452" cy="273" r="6" />
				</g>
			)}
		</>
	);
}
